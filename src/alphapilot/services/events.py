from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.db.models import DomainEvent


def emit(
    session: Session,
    *,
    symbol: str | None,
    event_type: str,
    title: str,
    direction: float = 0.0,
    strength: float = 0.5,
    summary: str | None = None,
    source_ref: str | None = None,
    occurred_at: datetime | None = None,
) -> DomainEvent:
    """Persist one normalized event, returning the existing row for a duplicate ref."""

    if not -1.0 <= direction <= 1.0:
        raise ValueError("event direction must be between -1 and 1")
    if not 0.0 <= strength <= 1.0:
        raise ValueError("event strength must be between 0 and 1")
    if source_ref:
        existing = session.scalar(
            select(DomainEvent).where(DomainEvent.source_ref == source_ref).limit(1)
        )
        if existing is not None:
            return existing

    event = DomainEvent(
        symbol=symbol,
        event_type=event_type,
        direction=direction,
        strength=strength,
        title=title,
        summary=summary,
        source_ref=source_ref,
        occurred_at=occurred_at or datetime.now(UTC),
    )
    session.add(event)
    session.flush()
    return event
