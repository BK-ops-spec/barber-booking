from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Callable

from . import availability as avail
from . import auth
from . import booking as booking_service
from . import config
from . import settings as shop_settings
from .booking import BookingError, BookingRequest
from .ratelimit import RateLimiter, RateLimitExceeded
from .timeutil import TimeError, local_to_utc, parse_date, to_utc_iso

_booking_limiter = RateLimiter(limit=20, window_seconds=3600)


@dataclass
class Request:
    method: str
    path: str
    conn: sqlite3.Connection = None  # type: ignore[assignment]
    query: dict[str, str] = field(default_factory=dict)
    body: dict = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    params: dict[str, str] = field(default_factory=dict)
    client_ip: str = ""

    def json_body(self) -> dict:
        return self.body if isinstance(self.body, dict) else {}

    def int_param(self, name: str, default: int | None = None) -> int | None:
        raw = self.query.get(name)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except ValueError:
            raise ApiError(400, f"Invalid value for {name}")


@dataclass
class Response:
    status: int = 200
    data: object = None
    headers: dict[str, str] = field(default_factory=dict)
    cookies: list[str] = field(default_factory=list)


class ApiError(Exception):
    def __init__(self, status: int, message: str, field_name: str = "", retry_after: int = 0):
        super().__init__(message)
        self.status = status
        self.message = message
        self.field_name = field_name
        self.retry_after = retry_after


Handler = Callable[[Request], Response]
_ROUTES: list[tuple[str, re.Pattern, Handler]] = []


def route(method: str, pattern: str) -> Callable[[Handler], Handler]:
    compiled = re.compile("^" + re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", pattern) + "$")

    def decorator(handler: Handler) -> Handler:
        _ROUTES.append((method.upper(), compiled, handler))
        return handler

    return decorator


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_EXEMPT_PATHS = {"/api/admin/login"}


def dispatch(conn: sqlite3.Connection, request: Request) -> Response:
    allowed = set()
    for method, pattern, handler in _ROUTES:
        match = pattern.match(request.path)
        if not match:
            continue
        if method != request.method:
            allowed.add(method)
            continue
        request.params = match.groupdict()
        if _needs_csrf(request.path, request.method):
            _enforce_csrf(request)
        return handler(request)
    if allowed:
        raise ApiError(405, "Method not allowed")
    raise ApiError(404, "Not found")


def _needs_csrf(path: str, method: str) -> bool:
    if method in SAFE_METHODS:
        return False
    if not path.startswith("/api/admin/"):
        return False
    return path not in CSRF_EXEMPT_PATHS


def _enforce_csrf(request: Request) -> None:
    token = request.cookies.get(config.SESSION_COOKIE)
    provided = request.headers.get("x-csrf-token", "")
    if not auth.verify_csrf(token, provided):
        raise ApiError(403, "Security token missing or invalid, please reload the page")


def require_admin(conn: sqlite3.Connection, request: Request) -> dict:
    token = request.cookies.get(config.SESSION_COOKIE)
    user = auth.session_user(conn, token)
    if user is None:
        raise ApiError(401, "Sign in required")
    return user


def _int_or_none(value):
    if value in (None, "", "null", "any", "Any"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError(400, "Invalid id")


def _date_or_none(value):
    if not value:
        return None
    try:
        return parse_date(value)
    except TimeError:
        raise ApiError(400, "Invalid date, expected YYYY-MM-DD")


@route("GET", "/api/config")
def get_config(request: Request) -> Response:
    conn = request.conn
    info = shop_settings.public_info(conn)
    info["services"] = [_service_dict(row) for row in avail.list_services(conn)]
    info["staff"] = [_staff_dict(conn, row) for row in avail.list_staff(conn)]
    info["lead_time_min"] = shop_settings.lead_time_minutes(conn)
    return Response(data=info)


@route("GET", "/api/services")
def get_services(request: Request) -> Response:
    conn = request.conn
    return Response(data=[_service_dict(row) for row in avail.list_services(conn)])


@route("GET", "/api/staff")
def get_staff(request: Request) -> Response:
    conn = request.conn
    service_id = _int_or_none(request.query.get("service_id"))
    rows = avail.staff_for_service(conn, service_id) if service_id else avail.list_staff(conn)
    return Response(data=[_staff_dict(conn, row) for row in rows])


def _service_dict(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "price_cents": row["price_cents"],
        "duration_min": row["duration_min"],
        "active": bool(row["active"]),
    }


def _staff_dict(conn: sqlite3.Connection, row) -> dict:
    service_ids = [
        r["service_id"]
        for r in conn.execute(
            "SELECT service_id FROM staff_services WHERE staff_id = ? ORDER BY service_id",
            (row["id"],),
        ).fetchall()
    ]
    return {
        "id": row["id"],
        "name": row["name"],
        "title": row["title"],
        "bio": row["bio"],
        "phone": row["phone"],
        "active": bool(row["active"]),
        "service_ids": service_ids,
    }


@route("GET", "/api/availability")
def get_availability(request: Request) -> Response:
    conn = request.conn
    service_id = request.int_param("service_id")
    if service_id is None:
        raise ApiError(400, "service_id is required")
    staff_id = _int_or_none(request.query.get("staff_id"))
    day = _date_or_none(request.query.get("date"))
    if day is None:
        day = datetime.now(shop_settings.zone(conn)).date()
    result = avail.availability_for_date(conn, day, service_id, staff_id)
    return Response(
        data={
            "date": result.day.isoformat(),
            "timezone": result.timezone,
            "closed": result.closed,
            "reason": result.reason,
            "slots": result.slots,
        }
    )


@route("GET", "/api/dates")
def get_dates(request: Request) -> Response:
    conn = request.conn
    service_id = request.int_param("service_id")
    if service_id is None:
        raise ApiError(400, "service_id is required")
    staff_id = _int_or_none(request.query.get("staff_id"))
    days = request.int_param("days", 30) or 30
    dates = avail.bookable_dates(conn, service_id, staff_id, min(days, 120))
    return Response(data={"dates": dates, "timezone": shop_settings.get(conn, "shop_timezone", "UTC")})


@route("POST", "/api/bookings")
def create_booking(request: Request) -> Response:
    conn = request.conn
    limit_key = f"booking:{request.client_ip or 'unknown'}"
    try:
        _booking_limiter.check(limit_key)
    except RateLimitExceeded as exc:
        raise ApiError(429, exc.message, retry_after=exc.retry_after)
    body = request.json_body()
    try:
        service_id = int(body.get("service_id"))
        day = parse_date(body.get("date"))
        start_local = str(body.get("time") or "")
    except (TypeError, ValueError, TimeError) as exc:
        raise ApiError(400, "Please choose a service, date and time")
    staff_id = _int_or_none(body.get("staff_id"))
    booking_request = BookingRequest(
        service_id=service_id,
        day=day,
        start_local=start_local,
        customer_name=str(body.get("customer_name") or ""),
        phone=str(body.get("phone") or ""),
        email=str(body.get("email") or ""),
        staff_id=staff_id,
        notes=str(body.get("notes") or ""),
    )
    try:
        appointment = booking_service.create_booking(conn, booking_request)
    except BookingError as exc:
        raise ApiError(400, exc.message, exc.field)
    _booking_limiter.hit(limit_key)
    return Response(status=201, data=appointment)


@route("GET", "/api/bookings/lookup")
def lookup_booking(request: Request) -> Response:
    conn = request.conn
    code = request.query.get("code", "")
    phone = request.query.get("phone", "")
    if not code or not phone:
        raise ApiError(400, "Enter your booking code and phone number")
    appointment = booking_service.find_by_code_and_phone(conn, code, phone)
    if appointment is None:
        raise ApiError(404, "No booking matches those details")
    return Response(data=appointment)


@route("POST", "/api/bookings/<code>/cancel")
def cancel_booking(request: Request) -> Response:
    conn = request.conn
    body = request.json_body()
    code = request.params["code"]
    phone = str(body.get("phone") or "")
    appointment = booking_service.find_by_code_and_phone(conn, code, phone)
    if appointment is None:
        raise ApiError(404, "No booking matches those details")
    updated = booking_service.cancel_appointment(conn, appointment["id"])
    return Response(data=updated)


@route("POST", "/api/admin/login")
def admin_login(request: Request) -> Response:
    conn = request.conn
    body = request.json_body()
    try:
        user = auth.authenticate(conn, str(body.get("username") or ""), str(body.get("password") or ""))
    except auth.RateLimitError as exc:
        raise ApiError(429, exc.message, retry_after=exc.retry_after)
    except auth.AuthError as exc:
        raise ApiError(401, exc.message)
    token, expires = auth.create_session(conn, user["id"])
    cookie = (
        f"{config.SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Expires={expires}"
    )
    if config.SESSION_COOKIE_SECURE:
        cookie += "; Secure"
    return Response(data={"user": user, "csrf_token": auth.csrf_token(token)}, cookies=[cookie])


@route("POST", "/api/admin/logout")
def admin_logout(request: Request) -> Response:
    conn = request.conn
    auth.destroy_session(conn, request.cookies.get(config.SESSION_COOKIE))
    return Response(
        data={"ok": True},
        cookies=[f"{config.SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"],
    )


@route("GET", "/api/admin/me")
def admin_me(request: Request) -> Response:
    conn = request.conn
    user = require_admin(conn, request)
    token = request.cookies.get(config.SESSION_COOKIE)
    return Response(data={"user": user, "csrf_token": auth.csrf_token(token)})


@route("POST", "/api/admin/password")
def admin_password(request: Request) -> Response:
    conn = request.conn
    user = require_admin(conn, request)
    body = request.json_body()
    try:
        auth.authenticate(conn, user["username"], str(body.get("current_password") or ""))
        auth.change_password(conn, user["id"], str(body.get("new_password") or ""))
    except auth.AuthError as exc:
        raise ApiError(400, exc.message)
    return Response(data={"ok": True})


@route("GET", "/api/admin/appointments")
def admin_appointments(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    day = _date_or_none(request.query.get("date"))
    staff_id = _int_or_none(request.query.get("staff_id"))
    status = request.query.get("status") or None
    search = request.query.get("q") or None
    rows = booking_service.list_appointments(conn, day, staff_id, status, search)
    return Response(data={"appointments": rows, "date": day.isoformat() if day else None})


@route("POST", "/api/admin/appointments")
def admin_create_booking(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    body = request.json_body()
    try:
        service_id = int(body.get("service_id"))
        day = parse_date(body.get("date"))
    except (TypeError, ValueError, TimeError):
        raise ApiError(400, "Please choose a service and date")
    booking_request = BookingRequest(
        service_id=service_id,
        day=day,
        start_local=str(body.get("time") or ""),
        customer_name=str(body.get("customer_name") or ""),
        phone=str(body.get("phone") or ""),
        email=str(body.get("email") or ""),
        staff_id=_int_or_none(body.get("staff_id")),
        notes=str(body.get("notes") or ""),
    )
    try:
        appointment = booking_service.create_booking(conn, booking_request)
    except BookingError as exc:
        raise ApiError(400, exc.message, exc.field)
    return Response(status=201, data=appointment)


@route("PATCH", "/api/admin/appointments/<id>")
def admin_update_booking(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    try:
        appointment_id = int(request.params["id"])
    except ValueError:
        raise ApiError(400, "Invalid booking id")
    body = request.json_body()
    try:
        if body.get("date") and body.get("time"):
            day = parse_date(body.get("date"))
            appointment = booking_service.reschedule(
                conn, appointment_id, day, str(body.get("time"))
            )
        elif body.get("status"):
            appointment = booking_service.set_status(conn, appointment_id, str(body["status"]))
        else:
            raise ApiError(400, "Nothing to update")
    except BookingError as exc:
        raise ApiError(400, exc.message, exc.field)
    except TimeError:
        raise ApiError(400, "Invalid date")
    return Response(data=appointment)


@route("POST", "/api/admin/appointments/<id>/cancel")
def admin_cancel_booking(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    try:
        appointment_id = int(request.params["id"])
    except ValueError:
        raise ApiError(400, "Invalid booking id")
    try:
        appointment = booking_service.cancel_appointment(conn, appointment_id)
    except BookingError as exc:
        raise ApiError(404, exc.message)
    return Response(data=appointment)


@route("GET", "/api/admin/services")
def admin_services(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    rows = conn.execute("SELECT * FROM services ORDER BY sort_order, name").fetchall()
    return Response(data=[_service_dict(row) for row in rows])


@route("POST", "/api/admin/services")
def admin_create_service(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    body = request.json_body()
    name = str(body.get("name") or "").strip()
    if not name:
        raise ApiError(400, "Service name is required", "name")
    price_cents = _price_to_cents(body)
    duration = int(body.get("duration_min") or 30)
    if duration < 5 or duration > 480:
        raise ApiError(400, "Duration must be between 5 and 480 minutes", "duration_min")
    sort_order = int(body.get("sort_order") or 0)
    cursor = conn.execute(
        "INSERT INTO services(name, description, price_cents, duration_min, active, sort_order) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (
            name,
            str(body.get("description") or "").strip(),
            price_cents,
            duration,
            1 if body.get("active", True) else 0,
            sort_order,
        ),
    )
    row = conn.execute("SELECT * FROM services WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return Response(status=201, data=_service_dict(row))


@route("PUT", "/api/admin/services/<id>")
def admin_update_service(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    try:
        service_id = int(request.params["id"])
    except ValueError:
        raise ApiError(400, "Invalid service id")
    body = request.json_body()
    row = conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()
    if row is None:
        raise ApiError(404, "Service not found")
    name = str(body.get("name", row["name"])).strip() or row["name"]
    price_cents = _price_to_cents(body) if ("price_cents" in body or "price" in body) else row["price_cents"]
    duration = int(body.get("duration_min", row["duration_min"]))
    active = 1 if body.get("active", bool(row["active"])) else 0
    conn.execute(
        "UPDATE services SET name = ?, description = ?, price_cents = ?, duration_min = ?, "
        "active = ?, sort_order = ? WHERE id = ?",
        (
            name,
            str(body.get("description", row["description"]) or ""),
            price_cents,
            duration,
            active,
            int(body.get("sort_order", row["sort_order"])),
            service_id,
        ),
    )
    updated = conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()
    return Response(data=_service_dict(updated))


@route("DELETE", "/api/admin/services/<id>")
def admin_delete_service(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    try:
        service_id = int(request.params["id"])
    except ValueError:
        raise ApiError(400, "Invalid service id")
    in_use = conn.execute(
        "SELECT COUNT(*) AS c FROM appointments WHERE service_id = ? AND status = 'confirmed'",
        (service_id,),
    ).fetchone()["c"]
    if in_use:
        conn.execute("UPDATE services SET active = 0 WHERE id = ?", (service_id,))
        return Response(data={"archived": True})
    conn.execute("DELETE FROM services WHERE id = ?", (service_id,))
    return Response(data={"deleted": True})


@route("GET", "/api/admin/staff")
def admin_staff(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    rows = conn.execute("SELECT * FROM staff ORDER BY sort_order, name").fetchall()
    return Response(data=[_staff_dict(conn, row) for row in rows])


@route("POST", "/api/admin/staff")
def admin_create_staff(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    body = request.json_body()
    name = str(body.get("name") or "").strip()
    if not name:
        raise ApiError(400, "Barber name is required", "name")
    cursor = conn.execute(
        "INSERT INTO staff(name, title, bio, phone, active, sort_order) VALUES(?, ?, ?, ?, ?, ?)",
        (
            name,
            str(body.get("title") or "").strip(),
            str(body.get("bio") or "").strip(),
            str(body.get("phone") or "").strip(),
            1 if body.get("active", True) else 0,
            int(body.get("sort_order") or 0),
        ),
    )
    staff_id = cursor.lastrowid
    for service_id in body.get("service_ids") or []:
        conn.execute(
            "INSERT INTO staff_services(staff_id, service_id) VALUES(?, ?) ON CONFLICT DO NOTHING",
            (staff_id, int(service_id)),
        )
    _seed_staff_hours(conn, staff_id)
    row = conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    return Response(status=201, data=_staff_dict(conn, row))


@route("PUT", "/api/admin/staff/<id>")
def admin_update_staff(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    try:
        staff_id = int(request.params["id"])
    except ValueError:
        raise ApiError(400, "Invalid barber id")
    body = request.json_body()
    row = conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    if row is None:
        raise ApiError(404, "Barber not found")
    conn.execute(
        "UPDATE staff SET name = ?, title = ?, bio = ?, phone = ?, active = ?, sort_order = ? WHERE id = ?",
        (
            str(body.get("name", row["name"]) or row["name"]).strip(),
            str(body.get("title", row["title"]) or ""),
            str(body.get("bio", row["bio"]) or ""),
            str(body.get("phone", row["phone"]) or ""),
            1 if body.get("active", bool(row["active"])) else 0,
            int(body.get("sort_order", row["sort_order"])),
            staff_id,
        ),
    )
    if "service_ids" in body:
        conn.execute("DELETE FROM staff_services WHERE staff_id = ?", (staff_id,))
        for service_id in body.get("service_ids") or []:
            conn.execute(
                "INSERT INTO staff_services(staff_id, service_id) VALUES(?, ?) ON CONFLICT DO NOTHING",
                (staff_id, int(service_id)),
            )
    updated = conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    return Response(data=_staff_dict(conn, updated))


@route("DELETE", "/api/admin/staff/<id>")
def admin_delete_staff(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    try:
        staff_id = int(request.params["id"])
    except ValueError:
        raise ApiError(400, "Invalid barber id")
    upcoming = conn.execute(
        "SELECT COUNT(*) AS c FROM appointments WHERE staff_id = ? AND status = 'confirmed'",
        (staff_id,),
    ).fetchone()["c"]
    if upcoming:
        conn.execute("UPDATE staff SET active = 0 WHERE id = ?", (staff_id,))
        return Response(data={"archived": True})
    conn.execute("DELETE FROM staff_hours WHERE staff_id = ?", (staff_id,))
    conn.execute("DELETE FROM staff_services WHERE staff_id = ?", (staff_id,))
    conn.execute("DELETE FROM staff WHERE id = ?", (staff_id,))
    return Response(data={"deleted": True})


def _seed_staff_hours(conn: sqlite3.Connection, staff_id: int) -> None:
    rows = conn.execute("SELECT * FROM shop_hours").fetchall()
    for row in rows:
        conn.execute(
            "INSERT INTO staff_hours(staff_id, day_of_week, start_time, end_time) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(staff_id, day_of_week) DO NOTHING",
            (staff_id, row["day_of_week"], row["start_time"], row["end_time"]),
        )


@route("GET", "/api/admin/hours")
def admin_hours(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    shop = conn.execute("SELECT * FROM shop_hours ORDER BY day_of_week").fetchall()
    staff_rows = conn.execute("SELECT * FROM staff ORDER BY sort_order, name").fetchall()
    out = []
    for member in staff_rows:
        hours = conn.execute(
            "SELECT * FROM staff_hours WHERE staff_id = ? ORDER BY day_of_week", (member["id"],)
        ).fetchall()
        out.append(
            {
                "staff_id": member["id"],
                "name": member["name"],
                "hours": [
                    {
                        "day_of_week": h["day_of_week"],
                        "start_time": h["start_time"],
                        "end_time": h["end_time"],
                    }
                    for h in hours
                ],
            }
        )
    return Response(
        data={
            "shop_hours": [
                {
                    "day_of_week": r["day_of_week"],
                    "start_time": r["start_time"],
                    "end_time": r["end_time"],
                }
                for r in shop
            ],
            "staff_hours": out,
        }
    )


@route("PUT", "/api/admin/hours")
def admin_update_hours(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    body = request.json_body()
    shop_hours = body.get("shop_hours")
    if shop_hours is not None:
        conn.execute("DELETE FROM shop_hours")
        for entry in shop_hours:
            conn.execute(
                "INSERT INTO shop_hours(day_of_week, start_time, end_time) VALUES(?, ?, ?)",
                (int(entry["day_of_week"]), entry["start_time"], entry["end_time"]),
            )
    for member in body.get("staff_hours") or []:
        staff_id = int(member["staff_id"])
        conn.execute("DELETE FROM staff_hours WHERE staff_id = ?", (staff_id,))
        for entry in member.get("hours") or []:
            conn.execute(
                "INSERT INTO staff_hours(staff_id, day_of_week, start_time, end_time) VALUES(?, ?, ?, ?)",
                (staff_id, int(entry["day_of_week"]), entry["start_time"], entry["end_time"]),
            )
    return Response(data={"ok": True})


@route("GET", "/api/admin/closures")
def admin_closures(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    rows = conn.execute("SELECT * FROM closures ORDER BY day").fetchall()
    return Response(data=[{"id": r["id"], "day": r["day"], "reason": r["reason"]} for r in rows])


@route("POST", "/api/admin/closures")
def admin_add_closure(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    body = request.json_body()
    try:
        day = parse_date(body.get("day"))
    except TimeError:
        raise ApiError(400, "Invalid date")
    conn.execute(
        "INSERT INTO closures(day, reason) VALUES(?, ?) ON CONFLICT(day) DO UPDATE SET reason = excluded.reason",
        (day.isoformat(), str(body.get("reason") or "").strip()),
    )
    return Response(status=201, data={"day": day.isoformat()})


@route("DELETE", "/api/admin/closures/<day>")
def admin_delete_closure(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    conn.execute("DELETE FROM closures WHERE day = ?", (request.params["day"],))
    return Response(data={"ok": True})


@route("GET", "/api/admin/settings")
def admin_settings(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    return Response(data=shop_settings.get_all(conn))


@route("PUT", "/api/admin/settings")
def admin_update_settings(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    body = request.json_body()
    allowed = set(shop_settings.get_all(conn).keys())
    updates = {k: str(v) for k, v in body.items() if k in allowed}
    shop_settings.set_many(conn, updates)
    return Response(data=shop_settings.get_all(conn))


@route("GET", "/api/admin/stats")
def admin_stats(request: Request) -> Response:
    conn = request.conn
    require_admin(conn, request)
    zone = shop_settings.zone(conn)
    today = datetime.now(zone).date()
    todays = booking_service.list_appointments(conn, today)
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=7)
    week_start_utc = to_utc_iso(local_to_utc(week_start, time(0, 0), zone))
    week_end_utc = to_utc_iso(local_to_utc(week_end, time(0, 0), zone))
    week_rows = conn.execute(
        "SELECT a.*, s.name AS service_name, s.duration_min AS duration_min, st.name AS staff_name "
        "FROM appointments a JOIN services s ON s.id = a.service_id JOIN staff st ON st.id = a.staff_id "
        "WHERE a.status = 'confirmed' AND a.start_utc >= ? AND a.start_utc < ?",
        (week_start_utc, week_end_utc),
    ).fetchall()
    revenue = sum(r["price_cents"] for r in week_rows)
    counts = conn.execute(
        "SELECT status, COUNT(*) AS c FROM appointments GROUP BY status"
    ).fetchall()
    return Response(
        data={
            "today_count": len([r for r in todays if r["status"] == "confirmed"]),
            "week_count": len(week_rows),
            "week_revenue_cents": revenue,
            "status_counts": {r["status"]: r["c"] for r in counts},
            "today": todays,
        }
    )


def _price_to_cents(body: dict) -> int:
    if "price_cents" in body:
        try:
            cents = int(body["price_cents"])
        except (TypeError, ValueError):
            raise ApiError(400, "Invalid price", "price_cents")
    elif "price" in body:
        try:
            cents = int(round(float(body["price"]) * 100))
        except (TypeError, ValueError):
            raise ApiError(400, "Invalid price", "price")
    else:
        cents = 0
    if cents < 0:
        raise ApiError(400, "Invalid price", "price_cents")
    return cents
