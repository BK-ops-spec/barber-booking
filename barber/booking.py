from __future__ import annotations

import random
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from . import availability as avail
from . import config
from . import settings as shop_settings
from .db import transaction
from .timeutil import (
    local_to_utc,
    minutes_since_midnight,
    minutes_to_time,
    parse_time,
    to_utc_iso,
)


class BookingError(Exception):
    def __init__(self, message: str, field: str = ""):
        super().__init__(message)
        self.message = message
        self.field = field


@dataclass
class BookingRequest:
    service_id: int
    day: date
    start_local: str
    customer_name: str
    phone: str
    email: str = ""
    staff_id: int | None = None
    notes: str = ""

    @property
    def start_time(self) -> time:
        return parse_time(self.start_local)


def normalize_phone(value: str) -> str:
    trimmed = (value or "").strip()
    digits = re.sub(r"\D", "", trimmed)
    if not digits:
        raise BookingError("A phone number is required", "phone")
    if len(digits) < 7:
        raise BookingError("That phone number looks too short", "phone")
    return digits


def normalize_email(value: str) -> str:
    trimmed = (value or "").strip()
    if not trimmed:
        return ""
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", trimmed):
        raise BookingError("That email address does not look valid", "email")
    return trimmed


def generate_code(conn: sqlite3.Connection) -> str:
    for _ in range(200):
        code = "".join(random.choice(config.CODE_ALPHABET) for _ in range(config.CODE_LENGTH))
        exists = conn.execute("SELECT 1 FROM appointments WHERE code = ?", (code,)).fetchone()
        if not exists:
            return code
    raise BookingError("Unable to generate a booking code, please retry")


def validate_request(conn: sqlite3.Connection, request: BookingRequest) -> dict:
    service = avail.service_by_id(conn, request.service_id)
    if service is None:
        raise BookingError("That service is not available", "service_id")

    zone = shop_settings.zone(conn)
    today = datetime.now(zone).date()
    if request.day < today:
        raise BookingError("That date is in the past", "date")
    limit = today + timedelta(days=shop_settings.max_advance_days(conn))
    if request.day > limit:
        raise BookingError(
            f"Bookings open up to {shop_settings.max_advance_days(conn)} days ahead", "date"
        )
    if avail.is_closed_day(conn, request.day):
        raise BookingError("The shop is closed on that date", "date")

    if request.staff_id is not None:
        staff = avail.staff_by_id(conn, request.staff_id)
        if staff is None:
            raise BookingError("That barber is not available", "staff_id")
        if not avail.staff_offers_service(conn, request.staff_id, request.service_id):
            raise BookingError("That barber does not offer this service", "staff_id")

    windows = avail.effective_windows(conn, request.staff_id, request.day)
    if not windows:
        raise BookingError("The shop is closed on that day", "date")

    duration = service["duration_min"]
    start_min = minutes_since_midnight(request.start_time)
    end_min = start_min + duration
    if end_min > 24 * 60:
        raise BookingError("That time slot is too close to closing", "time")
    fits = any(
        minutes_since_midnight(w.start) <= start_min and end_min <= minutes_since_midnight(w.end)
        for w in windows
    )
    if not fits:
        raise BookingError("That time is outside the barber's working hours", "time")

    now = datetime.now(zone)
    lead = timedelta(minutes=shop_settings.lead_time_minutes(conn))
    start_utc = local_to_utc(request.day, request.start_time, zone)
    if start_utc < now + lead:
        raise BookingError(
            f"Bookings need at least {shop_settings.lead_time_minutes(conn)} minutes notice", "time"
        )

    return {
        "service": service,
        "zone": zone,
        "start_utc": start_utc,
        "end_utc": local_to_utc(request.day, minutes_to_time(end_min), zone),
    }


def create_booking(conn: sqlite3.Connection, request: BookingRequest) -> dict:
    request.customer_name = (request.customer_name or "").strip()
    if len(request.customer_name) < 2:
        raise BookingError("Please enter your name", "customer_name")
    request.phone = normalize_phone(request.phone)
    request.email = normalize_email(request.email)
    request.notes = (request.notes or "").strip()[:500]

    context = validate_request(conn, request)

    with transaction(conn):
        staff_id = request.staff_id
        if staff_id is None:
            staff_id = avail.resolve_staff_for_slot(
                conn, request.day, request.service_id, None, request.start_local
            )
            if staff_id is None:
                raise BookingError("That time was just taken, please pick another", "time")

        conflicts = conn.execute(
            "SELECT 1 FROM appointments WHERE staff_id = ? AND status = 'confirmed' "
            "AND start_utc < ? AND end_utc > ? LIMIT 1",
            (staff_id, to_utc_iso(context["end_utc"]), to_utc_iso(context["start_utc"])),
        ).fetchone()
        if conflicts:
            raise BookingError("That time was just taken, please pick another", "time")

        code = generate_code(conn)
        cursor = conn.execute(
            "INSERT INTO appointments(code, customer_name, phone, email, staff_id, service_id, "
            "start_utc, end_utc, price_cents, status, notes, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?)",
            (
                code,
                request.customer_name,
                request.phone,
                request.email,
                staff_id,
                request.service_id,
                to_utc_iso(context["start_utc"]),
                to_utc_iso(context["end_utc"]),
                context["service"]["price_cents"],
                request.notes,
                to_utc_iso(datetime.now(timezone.utc)),
            ),
        )
        appointment_id = cursor.lastrowid

    return get_appointment(conn, appointment_id)


def appointment_dict(conn: sqlite3.Connection, row) -> dict:
    zone = shop_settings.zone(conn)
    start = datetime.fromisoformat(row["start_utc"])
    end = datetime.fromisoformat(row["end_utc"])
    return {
        "id": row["id"],
        "code": row["code"],
        "customer_name": row["customer_name"],
        "phone": row["phone"],
        "email": row["email"],
        "notes": row["notes"],
        "status": row["status"],
        "staff_id": row["staff_id"],
        "staff_name": row["staff_name"] if "staff_name" in row.keys() else "",
        "service_id": row["service_id"],
        "service_name": row["service_name"] if "service_name" in row.keys() else "",
        "duration_min": row["duration_min"] if "duration_min" in row.keys() else 0,
        "price_cents": row["price_cents"],
        "start_utc": row["start_utc"],
        "end_utc": row["end_utc"],
        "start_local": start.astimezone(zone).isoformat(timespec="seconds"),
        "end_local": end.astimezone(zone).isoformat(timespec="seconds"),
        "local_date": start.astimezone(zone).date().isoformat(),
        "local_time": start.astimezone(zone).strftime("%H:%M"),
        "local_end_time": end.astimezone(zone).strftime("%H:%M"),
        "created_at": row["created_at"],
    }


def _base_query() -> str:
    return (
        "SELECT a.*, s.name AS service_name, s.duration_min AS duration_min, "
        "st.name AS staff_name FROM appointments a "
        "JOIN services s ON s.id = a.service_id "
        "JOIN staff st ON st.id = a.staff_id "
    )


def get_appointment(conn: sqlite3.Connection, appointment_id: int) -> dict:
    row = conn.execute(_base_query() + "WHERE a.id = ?", (appointment_id,)).fetchone()
    if row is None:
        raise BookingError("Booking not found")
    return appointment_dict(conn, row)


def find_by_code(conn: sqlite3.Connection, code: str) -> dict | None:
    row = conn.execute(_base_query() + "WHERE a.code = ?", (code.strip().upper(),)).fetchone()
    if row is None:
        return None
    return appointment_dict(conn, row)


def find_by_code_and_phone(conn: sqlite3.Connection, code: str, phone: str) -> dict | None:
    digits = re.sub(r"\D", "", phone or "")
    row = conn.execute(
        _base_query() + "WHERE a.code = ? AND a.phone = ?",
        (code.strip().upper(), digits),
    ).fetchone()
    if row is None:
        return None
    return appointment_dict(conn, row)


def list_appointments(
    conn: sqlite3.Connection,
    day: date | None = None,
    staff_id: int | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 200,
) -> list[dict]:
    sql = _base_query() + "WHERE 1 = 1"
    params: list = []
    if day is not None:
        zone = shop_settings.zone(conn)
        start = local_to_utc(day, time(0, 0), zone)
        end = local_to_utc(day + timedelta(days=1), time(0, 0), zone)
        sql += " AND a.start_utc >= ? AND a.start_utc < ?"
        params.extend([to_utc_iso(start), to_utc_iso(end)])
    if staff_id is not None:
        sql += " AND a.staff_id = ?"
        params.append(staff_id)
    if status:
        sql += " AND a.status = ?"
        params.append(status)
    if search:
        sql += " AND (a.customer_name LIKE ? OR a.phone LIKE ? OR a.code LIKE ?)"
        needle = f"%{search.strip()}%"
        params.extend([needle, needle, f"%{search.strip().upper()}%"])
    sql += " ORDER BY a.start_utc LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [appointment_dict(conn, row) for row in rows]


def cancel_appointment(conn: sqlite3.Connection, appointment_id: int) -> dict:
    with transaction(conn):
        row = conn.execute("SELECT * FROM appointments WHERE id = ?", (appointment_id,)).fetchone()
        if row is None:
            raise BookingError("Booking not found")
        if row["status"] == "cancelled":
            return get_appointment(conn, appointment_id)
        conn.execute(
            "UPDATE appointments SET status = 'cancelled', cancelled_at = ? WHERE id = ?",
            (to_utc_iso(datetime.now(timezone.utc)), appointment_id),
        )
    return get_appointment(conn, appointment_id)


def set_status(conn: sqlite3.Connection, appointment_id: int, status: str) -> dict:
    if status not in {"confirmed", "completed", "cancelled", "no_show"}:
        raise BookingError("Unknown status")
    with transaction(conn):
        row = conn.execute("SELECT id FROM appointments WHERE id = ?", (appointment_id,)).fetchone()
        if row is None:
            raise BookingError("Booking not found")
        conn.execute(
            "UPDATE appointments SET status = ? WHERE id = ?", (status, appointment_id)
        )
    return get_appointment(conn, appointment_id)


def reschedule(conn: sqlite3.Connection, appointment_id: int, day: date, start_local: str) -> dict:
    row = conn.execute("SELECT * FROM appointments WHERE id = ?", (appointment_id,)).fetchone()
    if row is None:
        raise BookingError("Booking not found")
    request = BookingRequest(
        service_id=row["service_id"],
        day=day,
        start_local=start_local,
        customer_name=row["customer_name"],
        phone=row["phone"],
        email=row["email"],
        staff_id=row["staff_id"],
        notes=row["notes"],
    )
    context = validate_request(conn, request)
    with transaction(conn):
        conflicts = conn.execute(
            "SELECT 1 FROM appointments WHERE staff_id = ? AND status = 'confirmed' "
            "AND id != ? AND start_utc < ? AND end_utc > ? LIMIT 1",
            (request.staff_id, appointment_id, to_utc_iso(context["end_utc"]), to_utc_iso(context["start_utc"])),
        ).fetchone()
        if conflicts:
            raise BookingError("That time is already taken", "time")
        conn.execute(
            "UPDATE appointments SET start_utc = ?, end_utc = ? WHERE id = ?",
            (to_utc_iso(context["start_utc"]), to_utc_iso(context["end_utc"]), appointment_id),
        )
    return get_appointment(conn, appointment_id)
