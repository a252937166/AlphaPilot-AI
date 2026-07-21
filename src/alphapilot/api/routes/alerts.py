from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency, get_provider
from alphapilot.core.timeutil import iso_utc
from alphapilot.db.models import AlertRecord
from alphapilot.services import watchlist as watchlist_service

router = APIRouter(prefix="/v1/alerts", tags=["alerts"])


def _alert_payload(record: AlertRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "symbol": record.symbol,
        "action": record.action,
        "urgency": record.urgency,
        "confidence": record.confidence,
        "suggested_position_change": record.suggested_position_change,
        "target_low": record.target_low,
        "target_high": record.target_high,
        "suggested_notional": record.suggested_notional,
        "reasons": record.reasons,
        "invalidation": record.invalidation,
        "model_version": record.model_version,
        "as_of": iso_utc(record.as_of),
        "expires_at": iso_utc(record.expires_at),
        "acknowledged": record.acknowledged,
        "created_at": iso_utc(record.created_at),
    }


@router.get("")
def list_alerts(
    limit: int = Query(default=50, ge=1, le=200),
    action: str | None = Query(default=None),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    query = select(AlertRecord).order_by(AlertRecord.created_at.desc()).limit(limit)
    if action:
        query = query.where(AlertRecord.action == action.upper())
    records = session.scalars(query).all()
    return {"alerts": [_alert_payload(record) for record in records]}


@router.post("/refresh")
def refresh_alerts(
    provider: str | None = Query(default=None),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    selected = get_provider(provider)
    created = watchlist_service.refresh_alerts(session, selected)
    session.flush()
    return {"created": [_alert_payload(record) for record in created]}


@router.post("/{alert_id}/acknowledge")
def acknowledge_alert(
    alert_id: int,
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    record = session.get(AlertRecord, alert_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    record.acknowledged = True
    return {"alert": _alert_payload(record)}
