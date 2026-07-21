from __future__ import annotations

from datetime import UTC, datetime


def iso_utc(value: datetime | None) -> str | None:
    """Serialize a datetime as ISO-8601 with an explicit UTC offset.

    SQLite drops timezone info on round-trip, so naive datetimes read from the
    database are assumed to be UTC; without the offset the frontend would
    render UTC wall-clock as local time.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()
