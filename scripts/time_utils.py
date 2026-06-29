"""Time normalization helpers shared by monitors."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

CN_TZ = timezone(timedelta(hours=8))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp_to_utc_iso(value: Any) -> str:
    if value in (None, ""):
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    try:
        timestamp = int(float(raw))
    except ValueError:
        return ""
    if timestamp > 10_000_000_000:
        timestamp = timestamp // 1000
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def parse_datetime_to_utc_iso(value: Any) -> str:
    if value in (None, ""):
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    if raw.replace(".", "", 1).isdigit():
        parsed = timestamp_to_utc_iso(raw)
        if parsed:
            return parsed
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, IndexError, AttributeError):
        pass
    normalized = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CN_TZ)
    return dt.astimezone(timezone.utc).isoformat()
