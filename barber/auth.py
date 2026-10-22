from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from . import config
from .db import hash_password, verify_password
from .ratelimit import RateLimiter


class AuthError(Exception):
    def __init__(self, message: str = "Invalid credentials"):
        super().__init__(message)
        self.message = message


class RateLimitError(AuthError):
    def __init__(self, message: str = "Too many attempts, try again in a few minutes", retry_after: int = 60):
        super().__init__(message)
        self.retry_after = retry_after


MAX_FAILURES = 8
LOCKOUT_SECONDS = 300

_login_limiter = RateLimiter(MAX_FAILURES, LOCKOUT_SECONDS)


def _record_failure(key: str) -> None:
    _login_limiter.hit(key)


def _clear_failures(key: str) -> None:
    _login_limiter.reset(key)


def check_rate_limit(username: str) -> None:
    key = (username or "").strip().lower()
    if _login_limiter.count(key) >= MAX_FAILURES:
        raise RateLimitError(retry_after=_login_limiter.retry_after(key))


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> dict:
    key = (username or "").strip().lower()
    check_rate_limit(key)
    row = conn.execute(
        "SELECT * FROM admin_users WHERE username = ?", ((username or "").strip(),)
    ).fetchone()
    if row is None:
        hash_password(password or "")
        _record_failure(key)
        raise AuthError()
    if not verify_password(password or "", row["password_hash"], row["salt"]):
        _record_failure(key)
        raise AuthError()
    _clear_failures(key)
    return {"id": row["id"], "username": row["username"]}


def create_session(conn: sqlite3.Connection, user_id: int) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=config.SESSION_TTL_SECONDS)
    conn.execute(
        "INSERT INTO sessions(token, user_id, created_at, expires_at) VALUES(?, ?, ?, ?)",
        (token, user_id, now.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds")),
    )
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(timespec="seconds"),))
    return token, format_datetime(expires, usegmt=True)


def session_user(conn: sqlite3.Connection, token: str | None) -> dict | None:
    if not token:
        return None
    row = conn.execute(
        "SELECT s.token, s.expires_at, u.id, u.username FROM sessions s "
        "JOIN admin_users u ON u.id = s.user_id WHERE s.token = ?",
        (token,),
    ).fetchone()
    if row is None:
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        return None
    return {"id": row["id"], "username": row["username"]}


def destroy_session(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def csrf_token(session_token: str | None) -> str:
    if not session_token:
        return ""
    return hmac.new(
        session_token.encode("utf-8"), b"barber-shop-csrf-v1", hashlib.sha256
    ).hexdigest()


def verify_csrf(session_token: str | None, provided: str | None) -> bool:
    expected = csrf_token(session_token)
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected, provided)


def change_password(conn: sqlite3.Connection, user_id: int, new_password: str) -> None:
    if len(new_password or "") < 6:
        raise AuthError("Password must be at least 6 characters")
    password_hash, salt = hash_password(new_password)
    conn.execute(
        "UPDATE admin_users SET password_hash = ?, salt = ? WHERE id = ?",
        (password_hash, salt, user_id),
    )
