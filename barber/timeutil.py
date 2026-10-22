from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TIME_RE = re.compile(r"^(?P<h>\d{1,2}):(?P<m>\d{2})$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class TimeError(ValueError):
    pass


def get_zone(name: str) -> ZoneInfo | timezone:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return timezone.utc


def parse_time(value: str) -> time:
    match = TIME_RE.match((value or "").strip())
    if not match:
        raise TimeError(f"Invalid time: {value!r}")
    hour = int(match.group("h"))
    minute = int(match.group("m"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise TimeError(f"Invalid time: {value!r}")
    return time(hour=hour, minute=minute)


def format_time(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def parse_date(value: str) -> date:
    if not isinstance(value, str) or not DATE_RE.match(value.strip()):
        raise TimeError(f"Invalid date: {value!r}")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise TimeError(f"Invalid date: {value!r}") from exc


def parse_utc(value: str) -> datetime:
    text = (value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def to_utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def local_to_utc(day: date, moment: time, zone) -> datetime:
    naive = datetime.combine(day, moment)
    return naive.replace(tzinfo=zone).astimezone(timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def minutes_since_midnight(value: time) -> int:
    return value.hour * 60 + value.minute


def minutes_to_time(total: int) -> time:
    total = max(0, min(24 * 60 - 1, total))
    return time(hour=total // 60, minute=total % 60)


def day_range_utc(day: date, zone) -> tuple[datetime, datetime]:
    start = local_to_utc(day, time(0, 0), zone)
    end = local_to_utc(day + timedelta(days=1), time(0, 0), zone)
    return start, end


def overlaps(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    return start_a < end_b and start_b < end_a
