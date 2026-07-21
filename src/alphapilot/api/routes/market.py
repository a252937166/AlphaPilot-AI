from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import (
    db_session_dependency,
    futu_client_dependency,
    get_provider,
    settings_dependency,
)
from alphapilot.core.config import Settings
from alphapilot.data.base import DataProviderError
from alphapilot.domain.models import RegimeResult
from alphapilot.futu.client import FutuClient
from alphapilot.prediction.regime import MarketRegimeClassifier
from alphapilot.services import market_data
from alphapilot.services.sectors import SectorServiceError, market_breadth_from_sample

router = APIRouter(prefix="/v1/market", tags=["market"])


@router.get("/regime", response_model=RegimeResult)
def market_regime(
    symbol: str = Query(default="000001"),
    provider: str | None = Query(default=None),
    lookback_days: int = Query(default=260, ge=80, le=1500),
) -> RegimeResult:
    selected_provider = get_provider(provider)
    end = date.today()
    start = end - timedelta(days=int(lookback_days * 1.7))
    try:
        bars = selected_provider.get_daily_bars(symbol, start, end)
        return MarketRegimeClassifier().classify(symbol, bars)
    except (DataProviderError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/indices")
def market_indices(
    history_days: int = Query(default=60, ge=10, le=500),
    session: Session = Depends(db_session_dependency),
    settings: Settings = Depends(settings_dependency),
) -> dict[str, Any]:
    quotes = market_data.index_quotes(settings)
    history = market_data.index_history(session, settings, days=history_days)
    series: dict[str, list[dict[str, Any]]] = {}
    for symbol, frame in history.items():
        tail = frame.tail(history_days)
        series[symbol] = [
            {"date": str(record["date"])[:10], "close": record["close"]}
            for record in tail.to_dict(orient="records")
        ]
    return {
        "quotes": quotes,
        "series": series,
        "symbols": market_data.INDEX_SYMBOLS,
    }


@router.get("/breadth")
def market_breadth(
    client: FutuClient = Depends(futu_client_dependency),
) -> dict[str, Any]:
    try:
        return market_breadth_from_sample(client)
    except (SectorServiceError, Exception) as exc:
        raise HTTPException(status_code=503, detail=f"breadth unavailable: {exc}") from exc
