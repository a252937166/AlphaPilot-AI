from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency
from alphapilot.core.timeutil import iso_utc
from alphapilot.db.models import DomainEvent
from alphapilot.services.watchlist import normalize_symbol

router = APIRouter(prefix="/v1/events", tags=["events"])


@router.get("")
def list_events(
    symbol: str | None = Query(default=None),
    types: str | None = Query(default=None, description="逗号分隔的事件类型"),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    selected_symbol = normalize_symbol(symbol) if symbol else None
    selected_types = sorted(
        {item.strip() for item in (types or "").split(",") if item.strip()}
    )
    query = select(DomainEvent)
    if selected_symbol is not None:
        query = query.where(DomainEvent.symbol == selected_symbol)
    if selected_types:
        query = query.where(DomainEvent.event_type.in_(selected_types))
    rows = session.scalars(
        query.order_by(DomainEvent.occurred_at.desc(), DomainEvent.id.desc()).limit(limit)
    ).all()
    return {
        "symbol": selected_symbol,
        "types": selected_types,
        "limit": limit,
        "events": [
            {
                "id": row.id,
                "symbol": row.symbol,
                "event_type": row.event_type,
                "direction": row.direction,
                "strength": row.strength,
                "title": row.title,
                "summary": row.summary,
                "source_ref": row.source_ref,
                "occurred_at": iso_utc(row.occurred_at),
                "ingested_at": iso_utc(row.ingested_at),
            }
            for row in rows
        ],
    }
