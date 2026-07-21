from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency, get_provider
from alphapilot.core.timeutil import iso_utc
from alphapilot.services import watchlist as watchlist_service

router = APIRouter(prefix="/v1/watchlist", tags=["watchlist"])


class WatchlistUpsertRequest(BaseModel):
    symbol: str = Field(min_length=2, max_length=24)
    group_name: str | None = Field(default=None, max_length=32)
    display_name: str | None = Field(default=None, max_length=64)
    cost_price: float | None = Field(default=None, gt=0)
    quantity: float | None = Field(default=None, ge=0)
    thesis: str | None = None
    catalysts: list[str] | None = None
    risks: list[str] | None = None
    invalidation_rules: list[str] | None = None
    initial_confidence: float | None = Field(default=None, ge=0, le=1)
    thesis_state: str | None = Field(default=None, max_length=24)


def _item_payload(item: Any) -> dict[str, Any]:
    return {
        "symbol": item.symbol,
        "group_name": item.group_name,
        "display_name": item.display_name,
        "cost_price": item.cost_price,
        "quantity": item.quantity,
        "thesis": item.thesis,
        "catalysts": item.catalysts,
        "risks": item.risks,
        "invalidation_rules": item.invalidation_rules,
        "initial_confidence": item.initial_confidence,
        "thesis_state": item.thesis_state,
        "created_at": iso_utc(item.created_at),
        "updated_at": iso_utc(item.updated_at),
    }


@router.get("")
def list_watchlist(session: Session = Depends(db_session_dependency)) -> dict[str, Any]:
    items = watchlist_service.list_items(session)
    return {"items": [_item_payload(item) for item in items]}


@router.post("")
def upsert_watchlist(
    request: WatchlistUpsertRequest,
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    item = watchlist_service.upsert_item(
        session, request.model_dump(exclude_none=True)
    )
    session.flush()
    return {"item": _item_payload(item)}


@router.delete("/{symbol}")
def delete_watchlist(
    symbol: str,
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    removed = watchlist_service.remove_item(session, symbol)
    if not removed:
        raise HTTPException(status_code=404, detail=f"{symbol} is not in the watchlist")
    return {"removed": symbol}


@router.get("/track")
def track_watchlist(
    provider: str | None = Query(default=None),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    selected = get_provider(provider)
    rows = watchlist_service.tracked_overview(session, selected)
    return {"rows": rows, "count": len(rows)}
