from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from . import settings as shop_settings
from .timeutil import (
    format_time,
    local_to_utc,
    minutes_since_midnight,
    minutes_to_time,
    overlaps,
    parse_time,
    to_utc_iso,
)


@dataclass
class Window:
    start: time
    end: time


@dataclass
class BusyPeriod:
    start: datetime
    end: datetime


@dataclass
class AvailabilityResult:
    day: date
    timezone: str
    slots: list[str] = field(default_factory=list)
    closed: bool = False
    reason: str = ""


def service_by_id(conn: sqlite3.Connection, service_id: int):
    return conn.execute(
        "SELECT * FROM services WHERE id = ? AND active = 1", (service_id,)
    ).fetchone()


def staff_by_id(conn: sqlite3.Connection, staff_id: int):
    return conn.execute(
        "SELECT * FROM staff WHERE id = ? AND active = 1", (staff_id,)
    ).fetchone()


def list_services(conn: sqlite3.Connection, include_inactive: bool = False):
    sql = "SELECT * FROM services"
    if not include_inactive:
        sql += " WHERE active = 1"
    sql += " ORDER BY sort_order, name"
    return conn.execute(sql).fetchall()


def list_staff(conn: sqlite3.Connection, include_inactive: bool = False):
    sql = "SELECT * FROM staff"
    if not include_inactive:
        sql += " WHERE active = 1"
    sql += " ORDER BY sort_order, name"
    return conn.execute(sql).fetchall()


def staff_for_service(conn: sqlite3.Connection, service_id: int, staff_id: int | None = None):
    sql = (
        "SELECT s.* FROM staff s "
        "JOIN staff_services ss ON ss.staff_id = s.id "
        "WHERE ss.service_id = ? AND s.active = 1"
    )
    params: list = [service_id]
    if staff_id is not None:
        sql += " AND s.id = ?"
        params.append(staff_id)
    sql += " ORDER BY s.sort_order, s.name"
    return conn.execute(sql, params).fetchall()


def staff_offers_service(conn: sqlite3.Connection, staff_id: int, service_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM staff_services WHERE staff_id = ? AND service_id = ?",
        (staff_id, service_id),
    ).fetchone()
    return row is not None


def shop_windows_for(conn: sqlite3.Connection, day_of_week: int) -> list[Window]:
    rows = conn.execute(
        "SELECT start_time, end_time FROM shop_hours WHERE day_of_week = ?", (day_of_week,)
    ).fetchall()
    return [Window(parse_time(r["start_time"]), parse_time(r["end_time"])) for r in rows]


def staff_windows_for(conn: sqlite3.Connection, staff_id: int, day_of_week: int) -> list[Window]:
    rows = conn.execute(
        "SELECT start_time, end_time FROM staff_hours WHERE staff_id = ? AND day_of_week = ?",
        (staff_id, day_of_week),
    ).fetchall()
    return [Window(parse_time(r["start_time"]), parse_time(r["end_time"])) for r in rows]


def effective_windows(conn: sqlite3.Connection, staff_id: int | None, day: date) -> list[Window]:
    day_of_week = day.weekday()
    if staff_id is not None:
        windows = staff_windows_for(conn, staff_id, day_of_week)
        if windows:
            return windows
    return shop_windows_for(conn, day_of_week)


def is_closed_day(conn: sqlite3.Connection, day: date) -> bool:
    row = conn.execute("SELECT 1 FROM closures WHERE day = ?", (day.isoformat(),)).fetchone()
    return row is not None


def busy_periods(conn: sqlite3.Connection, staff_id: int, day: date, zone) -> list[BusyPeriod]:
    start_local = datetime.combine(day, time(0, 0), tzinfo=zone)
    end_local = start_local + timedelta(days=1)
    rows = conn.execute(
        "SELECT start_utc, end_utc FROM appointments "
        "WHERE staff_id = ? AND status = 'confirmed' AND start_utc < ? AND end_utc > ?",
        (staff_id, to_utc_iso(end_local), to_utc_iso(start_local)),
    ).fetchall()
    periods = []
    for row in rows:
        periods.append(
            BusyPeriod(
                datetime.fromisoformat(row["start_utc"]),
                datetime.fromisoformat(row["end_utc"]),
            )
        )
    return periods


def slot_times(
    conn: sqlite3.Connection,
    day: date,
    staff_id: int | None,
    duration_min: int,
    busy: list[BusyPeriod],
    zone,
    not_before: datetime | None = None,
) -> list[str]:
    windows = effective_windows(conn, staff_id, day)
    if not windows:
        return []
    interval = shop_settings.slot_interval(conn)
    duration = max(5, duration_min)
    results: list[str] = []
    seen: set[str] = set()

    for window in windows:
        window_start = minutes_since_midnight(window.start)
        window_end = minutes_since_midnight(window.end)
        if window_end - window_start < duration:
            continue
        cursor = window_start
        while cursor + duration <= window_end:
            slot_local = minutes_to_time(cursor)
            slot_start_utc = local_to_utc(day, slot_local, zone)
            slot_end_utc = local_to_utc(day, minutes_to_time(cursor + duration), zone)
            if not_before is not None and slot_start_utc < not_before:
                cursor += interval
                continue
            conflict = False
            for period in busy:
                if overlaps(slot_start_utc, slot_end_utc, period.start, period.end):
                    conflict = True
                    break
            if not conflict:
                label = format_time(slot_local)
                if label not in seen:
                    seen.add(label)
                    results.append(label)
            cursor += interval
    results.sort()
    return results


def availability_for_date(
    conn: sqlite3.Connection,
    day: date,
    service_id: int,
    staff_id: int | None = None,
    include_all_staff: bool = True,
) -> AvailabilityResult:
    zone = shop_settings.zone(conn)
    tz_name = shop_settings.get(conn, "shop_timezone", "UTC")

    service = service_by_id(conn, service_id)
    if service is None:
        return AvailabilityResult(day=day, timezone=tz_name, closed=True, reason="Service unavailable")

    if staff_id is not None:
        staff = staff_by_id(conn, staff_id)
        if staff is None:
            return AvailabilityResult(day=day, timezone=tz_name, closed=True, reason="Barber unavailable")
        if not staff_offers_service(conn, staff_id, service_id):
            return AvailabilityResult(day=day, timezone=tz_name, closed=True, reason="Barber does not offer this service")
        candidates = [staff_id]
    else:
        candidates = [row["id"] for row in staff_for_service(conn, service_id)]

    if not candidates:
        return AvailabilityResult(day=day, timezone=tz_name, closed=True, reason="No barber offers this service")

    if is_closed_day(conn, day):
        return AvailabilityResult(day=day, timezone=tz_name, closed=True, reason="Shop closed on this date")

    now = datetime.now(zone)
    lead = timedelta(minutes=shop_settings.lead_time_minutes(conn))
    not_before = now + lead

    union: set[str] = set()
    for candidate in candidates:
        busy = busy_periods(conn, candidate, day, zone)
        union.update(
            slot_times(
                conn,
                day,
                candidate,
                service["duration_min"],
                busy,
                zone,
                not_before=not_before,
            )
        )
    return AvailabilityResult(day=day, timezone=tz_name, slots=sorted(union))


def available_staff_ids(
    conn: sqlite3.Connection,
    day: date,
    service_id: int,
    staff_ids: list[int],
) -> list[int]:
    zone = shop_settings.zone(conn)
    service = service_by_id(conn, service_id)
    if service is None:
        return []
    now = datetime.now(zone)
    not_before = now + timedelta(minutes=shop_settings.lead_time_minutes(conn))
    result = []
    for staff_id in staff_ids:
        busy = busy_periods(conn, staff_id, day, zone)
        slots = slot_times(conn, day, staff_id, service["duration_min"], busy, zone, not_before=not_before)
        if slots:
            result.append(staff_id)
    return result


def resolve_staff_for_slot(
    conn: sqlite3.Connection,
    day: date,
    service_id: int,
    staff_id: int | None,
    start_local: str,
) -> int | None:
    if staff_id is not None:
        return staff_id if staff_id in available_staff_ids(conn, day, service_id, [staff_id]) else None
    zone = shop_settings.zone(conn)
    service = service_by_id(conn, service_id)
    if service is None:
        return None
    for candidate in staff_for_service(conn, service_id):
        busy = busy_periods(conn, candidate["id"], day, zone)
        slots = slot_times(conn, day, candidate["id"], service["duration_min"], busy, zone)
        if start_local in slots:
            return candidate["id"]
    return None


def bookable_dates(conn: sqlite3.Connection, service_id: int, staff_id: int | None, days: int = 21) -> list[str]:
    zone = shop_settings.zone(conn)
    today = datetime.now(zone).date()
    limit = min(days, shop_settings.max_advance_days(conn))
    out = []
    for offset in range(limit + 1):
        day = today + timedelta(days=offset)
        result = availability_for_date(conn, day, service_id, staff_id)
        if result.slots:
            out.append(day.isoformat())
    return out
