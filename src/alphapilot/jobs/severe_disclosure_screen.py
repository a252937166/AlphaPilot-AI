"""Turn severe regulatory CNInfo announcements into negative domain events.

The P4.1 news poll stores every CNInfo announcement in ``news_items`` but
nothing downstream read them. This job scans the recent announcements, applies
the title rules in ``services.severe_disclosure`` and emits one negative
``DomainEvent`` per matching announcement, keyed by ``news:<id>`` so re-runs are
idempotent. ``occurred_at`` is the announcement's point-in-time ``available_time``
(the ingestion instant) and is never back-dated. An announcement whose own
publication date is older than the lookback (a poller catch-up backfilling old
days) is counted as stale and skipped: it is history, not a fresh warning.
Events with strength >= 0.6 raise a notification, and the thesis-drift engine
already treats any event with direction <= -0.5 inside its lookback as a reason
to re-examine a holding.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any

from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from alphapilot.db.engine import get_session
from alphapilot.db.models import DomainEvent, NewsItem
from alphapilot.jobs.registry import JobSpec, register
from alphapilot.services.events import emit
from alphapilot.services.severe_disclosure import (
    SEVERE_EVENT_TYPE,
    classify_severe_disclosure,
    severe_event_summary,
)

LOOKBACK_DAYS = 7
SOURCE = "cninfo"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def run_severe_disclosure_screen(
    *, now: datetime | None = None, lookback_days: int = LOOKBACK_DAYS
) -> dict[str, Any]:
    """Scan recent CNInfo announcements and emit severe negative events."""

    started = monotonic()
    current = _as_utc(now or datetime.now(UTC))
    floor = current - timedelta(days=lookback_days)
    stats: dict[str, Any] = {
        "lookback_days": lookback_days,
        "scanned": 0,
        "matched": 0,
        "emitted": 0,
        "existing": 0,
        "unsymboled": 0,
        "stale_skipped": 0,
        "by_subtype": {},
    }
    with get_session() as session:
        rows = session.scalars(
            select(NewsItem)
            .where(NewsItem.source == SOURCE, NewsItem.available_time >= floor)
            .order_by(NewsItem.id)
        ).all()
        for item in rows:
            stats["scanned"] += 1
            found = classify_severe_disclosure(item.title)
            if found is None:
                continue
            stats["matched"] += 1
            by_subtype = stats["by_subtype"]
            by_subtype[found.subtype] = by_subtype.get(found.subtype, 0) + 1
            if item.symbol is None:
                stats["unsymboled"] += 1
                continue
            if item.published_at is not None and _as_utc(item.published_at) < floor:
                stats["stale_skipped"] += 1
                continue
            source_ref = f"news:{item.id}"
            already = session.scalar(
                select(DomainEvent.id).where(DomainEvent.source_ref == source_ref).limit(1)
            )
            if already is not None:
                stats["existing"] += 1
                continue
            emit(
                session,
                symbol=item.symbol,
                event_type=SEVERE_EVENT_TYPE,
                title=item.title,
                direction=found.direction,
                strength=found.strength,
                summary=severe_event_summary(found, item.title),
                source_ref=source_ref,
                occurred_at=_as_utc(item.available_time),
            )
            stats["emitted"] += 1
    stats["duration_seconds"] = round(monotonic() - started, 2)
    return stats


def register_severe_disclosure_screen_job() -> None:
    register(
        JobSpec(
            name="severe_disclosure_screen",
            func=run_severe_disclosure_screen,
            trigger=IntervalTrigger(minutes=10),
            enabled_key="severe_disclosure_screen_enabled",
            misfire_grace_time=300,
        )
    )
