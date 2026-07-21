from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from alphapilot.alerts.service import AlertService
from alphapilot.api.dependencies import (
    cninfo_client_dependency,
    db_session_dependency,
    get_provider,
)
from alphapilot.cninfo.client import CninfoClient, CninfoError
from alphapilot.data.base import DataProviderError
from alphapilot.domain.models import StockAlert, StockForecast
from alphapilot.prediction.baseline import BaselineForecastEngine
from alphapilot.services import disclosures as disclosure_service
from alphapilot.services.market_data import get_bars_with_cache
from alphapilot.services.watchlist import normalize_symbol

router = APIRouter(prefix="/v1/stocks", tags=["stocks"])


def _forecast(
    session: Session, symbol: str, provider_name: str | None, lookback_days: int
) -> StockForecast:
    provider = get_provider(provider_name)
    end = date.today()
    start = end - timedelta(days=int(lookback_days * 1.7))
    try:
        result = get_bars_with_cache(session, provider, symbol, start, end)
        forecast = BaselineForecastEngine().forecast(symbol, result["frame"], result["source"])
        forecast.warnings.extend(result["warnings"])
        return forecast
    except (DataProviderError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{symbol}/forecast", response_model=StockForecast)
def stock_forecast(
    symbol: str,
    provider: str | None = Query(default=None),
    lookback_days: int = Query(default=220, ge=80, le=1500),
    session: Session = Depends(db_session_dependency),
) -> StockForecast:
    return _forecast(session, symbol, provider, lookback_days)


@router.get("/{symbol}/alert", response_model=StockAlert)
def stock_alert(
    symbol: str,
    provider: str | None = Query(default=None),
    lookback_days: int = Query(default=220, ge=80, le=1500),
    session: Session = Depends(db_session_dependency),
) -> StockAlert:
    return AlertService().evaluate(_forecast(session, symbol, provider, lookback_days))


@router.get("/{symbol}/bars")
def stock_bars(
    symbol: str,
    provider: str | None = Query(default=None),
    days: int = Query(default=120, ge=30, le=1000),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    selected = get_provider(provider)
    end = date.today()
    start = end - timedelta(days=int(days * 1.7))
    try:
        result = get_bars_with_cache(session, selected, symbol, start, end)
    except (DataProviderError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    frame = result["frame"].tail(days)
    return {
        "symbol": symbol,
        "source": result["source"],
        "warnings": result["warnings"],
        "bars": [
            {
                "date": str(record["date"])[:10],
                "open": record["open"],
                "high": record["high"],
                "low": record["low"],
                "close": record["close"],
                "volume": record["volume"],
                "amount": record.get("amount"),
            }
            for record in frame.to_dict(orient="records")
        ],
    }


@router.get("/{symbol}/overview")
def stock_overview(
    symbol: str,
    provider: str | None = Query(default=None),
    session: Session = Depends(db_session_dependency),
    cninfo: CninfoClient = Depends(cninfo_client_dependency),
) -> dict[str, Any]:
    code = normalize_symbol(symbol)
    forecast = _forecast(session, code, provider, 220)
    alert = AlertService().evaluate(forecast)

    selected = get_provider(provider)
    quote: dict[str, Any] | None = None
    try:
        snapshot = selected.get_snapshot([code])
        records = snapshot.to_dict(orient="records")
        if records:
            record = records[0]
            quote = {
                "last": record.get("last"),
                "change_pct": record.get("change_pct"),
                "volume": record.get("volume"),
                "amount": record.get("amount"),
            }
    except Exception:  # quote is optional; forecast already carries warnings
        quote = None

    security = disclosure_service.get_security(session, code)
    if security is None:
        try:
            disclosure_service.enrich_security(session, cninfo, code)
            security = disclosure_service.get_security(session, code)
        except CninfoError:
            security = None
    disclosures = disclosure_service.list_disclosures(session, code, limit=10)

    return {
        "symbol": code,
        "security": security,
        "quote": quote,
        "forecast": forecast.model_dump(mode="json"),
        "alert": alert.model_dump(mode="json"),
        "disclosures": disclosures,
    }
