from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from . import config


def _adapt_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _adapt_date(value: date) -> str:
    return value.isoformat()


sqlite3.register_adapter(datetime, _adapt_datetime)
sqlite3.register_adapter(date, _adapt_date)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS services (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    price_cents INTEGER NOT NULL DEFAULT 0,
    duration_min INTEGER NOT NULL DEFAULT 30,
    active      INTEGER NOT NULL DEFAULT 1,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS staff (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT    NOT NULL,
    title    TEXT    NOT NULL DEFAULT '',
    bio      TEXT    NOT NULL DEFAULT '',
    phone    TEXT    NOT NULL DEFAULT '',
    active   INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS staff_services (
    staff_id   INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    PRIMARY KEY (staff_id, service_id)
);

CREATE TABLE IF NOT EXISTS staff_hours (
    staff_id   INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    day_of_week INTEGER NOT NULL,
    start_time TEXT    NOT NULL,
    end_time   TEXT    NOT NULL,
    PRIMARY KEY (staff_id, day_of_week)
);

CREATE TABLE IF NOT EXISTS shop_hours (
    day_of_week INTEGER PRIMARY KEY,
    start_time  TEXT NOT NULL,
    end_time    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS closures (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    day    TEXT NOT NULL UNIQUE,
    reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS appointments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT    NOT NULL UNIQUE,
    customer_name TEXT NOT NULL,
    phone      TEXT    NOT NULL,
    email      TEXT    NOT NULL DEFAULT '',
    staff_id   INTEGER NOT NULL REFERENCES staff(id),
    service_id INTEGER NOT NULL REFERENCES services(id),
    start_utc  TEXT    NOT NULL,
    end_utc    TEXT    NOT NULL,
    price_cents INTEGER NOT NULL DEFAULT 0,
    status     TEXT    NOT NULL DEFAULT 'confirmed',
    notes      TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL,
    cancelled_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_appts_staff_start ON appointments(staff_id, start_utc);
CREATE INDEX IF NOT EXISTS idx_appts_start ON appointments(start_utc);
CREATE INDEX IF NOT EXISTS idx_appts_phone ON appointments(phone);

CREATE TABLE IF NOT EXISTS admin_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "shop_name": "Sons of Detroit",
    "shop_tagline": "Classic cuts, modern craft",
    "shop_phone": "(555) 018-2233",
    "shop_address": "412 Rivington Ave, Brooklyn, NY",
    "shop_timezone": "America/New_York",
    "open_time": "09:00",
    "close_time": "19:00",
    "slot_interval_min": 15,
    "lead_time_min": 60,
    "max_advance_days": 60,
    "allow_any_staff": "1",
    "currency": "USD",
}

SEED_SERVICES = [
    ("Signature Cut", "Consultation, precision cut, wash and finish.", 3800, 45, 1),
    ("Skin Fade", "Sharp gradient fade with razor finish.", 4200, 45, 2),
    ("Beard Sculpt", "Shape, line up and hot towel conditioning.", 2800, 30, 3),
    ("Cut & Beard", "Full cut paired with a beard sculpt.", 6000, 75, 4),
    ("Hot Towel Shave", "Traditional straight razor shave with hot towels.", 3500, 30, 5),
    ("Kids Cut", "For ages 12 and under, with a treat on the house.", 2200, 30, 6),
]

SEED_STAFF = [
    ("Marcus Holloway", "Master Barber", "Twelve years behind the chair, specialising in fades and razor work.", 1),
    ("Dante Reyes", "Senior Barber", "Classic scissor work, pompadours and vintage cuts.", 2),
    ("Ivy Chen", "Barber & Stylist", "Precision cuts with a focus on texture and styling.", 3),
]

SEED_STAFF_SERVICES = {
    1: [1, 2, 3, 4, 5, 6],
    2: [1, 2, 4, 6],
    3: [1, 3, 4, 6],
}

SEED_HOURS = {
    1: [("09:00", "19:00")],
    2: [("09:00", "19:00")],
    3: [("10:00", "18:00")],
    4: [("09:00", "18:00")],
    5: [("09:00", "19:00")],
    6: [("09:00", "17:00")],
    7: [("10:00", "16:00")],
}


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(path) if path else config.DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    _restrict_permissions(target)
    return conn


def _restrict_permissions(target: Path) -> None:
    try:
        os.chmod(target, 0o600)
        for suffix in ("-wal", "-shm"):
            sidecar = target.with_name(target.name + suffix)
            if sidecar.exists():
                os.chmod(sidecar, 0o600)
    except OSError:
        pass


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or os.urandom(16).hex()
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt.encode("utf-8"),
        n=2**14,
        r=8,
        p=1,
        dklen=64,
    )
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, password_hash)


def initialize(conn: sqlite3.Connection, seed: bool = True) -> None:
    conn.executescript(SCHEMA)
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO NOTHING",
            (key, value),
        )
    for day_of_week, windows in SEED_HOURS.items():
        for start, end in windows:
            conn.execute(
                "INSERT INTO shop_hours(day_of_week, start_time, end_time) VALUES(?, ?, ?) "
                "ON CONFLICT(day_of_week) DO NOTHING",
                (day_of_week, start, end),
            )
    if seed:
        _seed_catalog(conn)
        _seed_admin(conn)


def _seed_catalog(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) AS c FROM services").fetchone()["c"] == 0:
        conn.executemany(
            "INSERT INTO services(name, description, price_cents, duration_min, sort_order) "
            "VALUES(?, ?, ?, ?, ?)",
            SEED_SERVICES,
        )
    if conn.execute("SELECT COUNT(*) AS c FROM staff").fetchone()["c"] == 0:
        conn.executemany(
            "INSERT INTO staff(name, title, bio, phone, sort_order) VALUES(?, ?, ?, ?, ?)",
            [(name, title, bio, f"(555) 010-{1000 + i * 111}", i) for i, (name, title, bio, _) in enumerate(SEED_STAFF)],
        )
    for staff_id, service_ids in SEED_STAFF_SERVICES.items():
        conn.executemany(
            "INSERT INTO staff_services(staff_id, service_id) VALUES(?, ?) ON CONFLICT DO NOTHING",
            [(staff_id, sid) for sid in service_ids],
        )
    for staff_id in (1, 2, 3):
        for day_of_week in range(7):
            if day_of_week == 6:
                continue
            windows = SEED_HOURS.get(day_of_week, [])
            if not windows:
                continue
            for start, end in windows:
                conn.execute(
                    "INSERT INTO staff_hours(staff_id, day_of_week, start_time, end_time) "
                    "VALUES(?, ?, ?, ?) ON CONFLICT(staff_id, day_of_week) DO NOTHING",
                    (staff_id, day_of_week, start, end),
                )


def _seed_admin(conn: sqlite3.Connection) -> None:
    exists = conn.execute(
        "SELECT COUNT(*) AS c FROM admin_users WHERE username = ?",
        (config.DEFAULT_ADMIN_USERNAME,),
    ).fetchone()["c"]
    if exists:
        return
    password_hash, salt = hash_password(config.DEFAULT_ADMIN_PASSWORD)
    conn.execute(
        "INSERT INTO admin_users(username, password_hash, salt, created_at) VALUES(?, ?, ?, datetime('now'))",
        (config.DEFAULT_ADMIN_USERNAME, password_hash, salt),
    )
