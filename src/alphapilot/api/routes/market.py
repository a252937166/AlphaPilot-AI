from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

from alphapilot.api.dependencies import get_provider
from alphapilot.data.base import DataProviderError
from alphapilot.domain.models import RegimeResult
from alphapilot.prediction.regime import MarketRegimeClassifier

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
