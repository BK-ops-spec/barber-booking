from __future__ import annotations

import sqlite3

from .timeutil import get_zone


def get_all(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def get(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_value(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def set_many(conn: sqlite3.Connection, values: dict[str, str]) -> None:
    for key, value in values.items():
        set_value(conn, key, value)


def zone(conn: sqlite3.Connection):
    return get_zone(get(conn, "shop_timezone", "UTC"))


def slot_interval(conn: sqlite3.Connection) -> int:
    return max(5, int(get(conn, "slot_interval_min", "15")))


def lead_time_minutes(conn: sqlite3.Connection) -> int:
    return max(0, int(get(conn, "lead_time_min", "60")))


def max_advance_days(conn: sqlite3.Connection) -> int:
    return max(1, int(get(conn, "max_advance_days", "60")))


def allows_any_staff(conn: sqlite3.Connection) -> bool:
    return get(conn, "allow_any_staff", "1").strip().lower() in {"1", "true", "yes", "on"}


def public_info(conn: sqlite3.Connection) -> dict:
    data = get_all(conn)
    return {
        "shop_name": data.get("shop_name", ""),
        "shop_tagline": data.get("shop_tagline", ""),
        "shop_phone": data.get("shop_phone", ""),
        "shop_address": data.get("shop_address", ""),
        "timezone": data.get("shop_timezone", "UTC"),
        "currency": data.get("currency", "USD"),
        "open_time": data.get("open_time", "09:00"),
        "close_time": data.get("close_time", "19:00"),
        "allow_any_staff": allows_any_staff(conn),
        "max_advance_days": max_advance_days(conn),
    }


def price_range(conn: sqlite3.Connection) -> tuple[int, int]:
    row = conn.execute(
        "SELECT MIN(price_cents) AS lo, MAX(price_cents) AS hi FROM services WHERE active = 1"
    ).fetchone()
    return (row["lo"] or 0, row["hi"] or 0)
