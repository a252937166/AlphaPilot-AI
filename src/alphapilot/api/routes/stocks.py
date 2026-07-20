from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

from alphapilot.alerts.service import AlertService
from alphapilot.api.dependencies import get_provider
from alphapilot.data.base import DataProviderError
from alphapilot.domain.models import StockAlert, StockForecast
from alphapilot.prediction.baseline import BaselineForecastEngine

router = APIRouter(prefix="/v1/stocks", tags=["stocks"])


def _forecast(symbol: str, provider_name: str | None, lookback_days: int) -> StockForecast:
    provider = get_provider(provider_name)
    end = date.today()
    start = end - timedelta(days=int(lookback_days * 1.7))
    try:
        bars = provider.get_daily_bars(symbol, start, end)
        return BaselineForecastEngine().forecast(symbol, bars, provider.name)
    except (DataProviderError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{symbol}/forecast", response_model=StockForecast)
def stock_forecast(
    symbol: str,
    provider: str | None = Query(default=None),
    lookback_days: int = Query(default=220, ge=80, le=1500),
) -> StockForecast:
    return _forecast(symbol, provider, lookback_days)


@router.get("/{symbol}/alert", response_model=StockAlert)
def stock_alert(
    symbol: str,
    provider: str | None = Query(default=None),
    lookback_days: int = Query(default=220, ge=80, le=1500),
) -> StockAlert:
    return AlertService().evaluate(_forecast(symbol, provider, lookback_days))
