from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import (
    db_session_dependency,
    futu_client_dependency,
    get_provider,
    settings_dependency,
)
from alphapilot.core.config import Settings
from alphapilot.core.timeutil import iso_utc
from alphapilot.data.base import DataProviderError
from alphapilot.db.models import MarketSnapshotAgg
from alphapilot.domain.models import RegimeResult
from alphapilot.futu.client import FutuClient
from alphapilot.prediction.regime import MarketRegimeClassifier
from alphapilot.services import market_data
from alphapilot.services.sectors import SectorServiceError, market_breadth_from_sample

router = APIRouter(prefix="/v1/market", tags=["market"])
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _as_utc(value: datetime) -> datetime:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).astimezone(UTC)


def _serialize_full_breadth(
    latest: MarketSnapshotAgg,
    previous: MarketSnapshotAgg | None,
) -> dict[str, Any]:
    amount_delta = latest.total_amount - previous.total_amount if previous is not None else None
    amount_delta_pct = (
        amount_delta / previous.total_amount * 100
        if amount_delta is not None and previous is not None and previous.total_amount > 0
        else None
    )
    return {
        "ts": iso_utc(latest.ts),
        "advancers": latest.advancers,
        "decliners": latest.decliners,
        "unchanged": latest.unchanged,
        "limit_up": latest.limit_up,
        "limit_down": latest.limit_down,
        "broken_boards": latest.broken_boards,
        "up_gt4": latest.up_gt4,
        "down_gt4": latest.down_gt4,
        "total_amount": latest.total_amount,
        "avg_change_pct": latest.avg_change_pct,
        "median_change_pct": latest.median_change_pct,
        "source": latest.source,
        "prior_ts": iso_utc(previous.ts) if previous is not None else None,
        "prior_total_amount": previous.total_amount if previous is not None else None,
        "amount_delta": amount_delta,
        "amount_delta_pct": amount_delta_pct,
    }


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


@router.get("/breadth-full")
def market_breadth_full(
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    latest = session.scalars(
        select(MarketSnapshotAgg)
        .order_by(MarketSnapshotAgg.ts.desc(), MarketSnapshotAgg.id.desc())
        .limit(1)
    ).first()
    if latest is None:
        raise HTTPException(
            status_code=404,
            detail="暂无全市场宽度数据，请先运行全市场快照任务。",
        )

    latest_utc = _as_utc(latest.ts)
    latest_local = latest_utc.astimezone(MARKET_TIMEZONE)
    local_day_start = datetime.combine(
        latest_local.date(), time.min, tzinfo=MARKET_TIMEZONE
    ).astimezone(UTC)
    prior_candidates = session.scalars(
        select(MarketSnapshotAgg)
        .where(MarketSnapshotAgg.ts < local_day_start)
        .order_by(MarketSnapshotAgg.ts.desc(), MarketSnapshotAgg.id.desc())
        .limit(1000)
    ).all()
    previous: MarketSnapshotAgg | None = None
    if prior_candidates:
        prior_day = max(
            _as_utc(item.ts).astimezone(MARKET_TIMEZONE).date() for item in prior_candidates
        )
        same_day = [
            item
            for item in prior_candidates
            if _as_utc(item.ts).astimezone(MARKET_TIMEZONE).date() == prior_day
        ]
        target_seconds = latest_local.hour * 3600 + latest_local.minute * 60 + latest_local.second
        previous = min(
            same_day,
            key=lambda item: abs(
                (
                    _as_utc(item.ts).astimezone(MARKET_TIMEZONE).hour * 3600
                    + _as_utc(item.ts).astimezone(MARKET_TIMEZONE).minute * 60
                    + _as_utc(item.ts).astimezone(MARKET_TIMEZONE).second
                )
                - target_seconds
            ),
        )
    return _serialize_full_breadth(latest, previous)
