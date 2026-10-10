from __future__ import annotations

from datetime import datetime, timezone


def coerce_utc(value: datetime | None) -> datetime | None:
    """Return ``value`` as an aware UTC datetime (SQLite hands back naive timestamps)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)
