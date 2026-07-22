from __future__ import annotations

from collections.abc import Mapping
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
from alphapilot.db.models import MarketSentiment, MarketSnapshotAgg
from alphapilot.domain.models import RegimeResult
from alphapilot.engines.sentiment import liquidity_label, money_effect_label
from alphapilot.futu.client import FutuClient, FutuClientError
from alphapilot.prediction.regime import MarketRegimeClassifier
from alphapilot.services import market_data
from alphapilot.services.cross_market import cross_market_snapshot
from alphapilot.services.market_monitor import build_feed
from alphapilot.services.sectors import SectorServiceError, market_breadth_from_sample

router = APIRouter(prefix="/v1/market", tags=["market"])
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
INDEX_INTRADAY_SYMBOLS = {entry["symbol"] for entry in market_data.INDEX_SYMBOLS}


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


@router.get("/cross")
def market_cross(
    client: FutuClient = Depends(futu_client_dependency),
) -> dict[str, Any]:
    return cross_market_snapshot(client)


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


@router.get("/sentiment")
def market_sentiment(
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    row = session.scalars(
        select(MarketSentiment)
        .order_by(MarketSentiment.ts.desc(), MarketSentiment.id.desc())
        .limit(1)
    ).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="暂无市场情绪数据，请先运行全市场快照任务。",
        )
    latest_snapshot_id = session.scalar(
        select(MarketSnapshotAgg.id)
        .order_by(MarketSnapshotAgg.ts.desc(), MarketSnapshotAgg.id.desc())
        .limit(1)
    )
    if latest_snapshot_id != row.source_snapshot_id:
        raise HTTPException(
            status_code=503,
            detail="市场情绪尚未与最新全市场快照同步，请重新运行全市场快照任务。",
        )

    details = dict(row.details) if isinstance(row.details, Mapping) else {}
    components_value = details.get("components")
    components = dict(components_value) if isinstance(components_value, Mapping) else {}
    history_samples = {
        str(name): int(component.get("historical_samples", 0))
        for name, component in components.items()
        if isinstance(component, Mapping)
    }
    sample_sizes = {
        str(name): int(component.get("sample_size", 0))
        for name, component in components.items()
        if isinstance(component, Mapping)
    }
    degraded_value = details.get("degraded_components")
    missing_value = details.get("missing_inputs")
    degraded_components = (
        [str(item) for item in degraded_value]
        if isinstance(degraded_value, list)
        else ["audit_details"]
    )
    missing_inputs = (
        [str(item) for item in missing_value]
        if isinstance(missing_value, list)
        else ["audit_details"]
    )
    subs = {
        "breadth": row.breadth_sub,
        "limitup": row.limitup_sub,
        "volume": row.volume_sub,
        "volatility": row.volatility_sub,
    }
    return {
        "score": row.score,
        "label": row.label,
        "subs": subs,
        "money_effect": money_effect_label(row.limitup_sub),
        "liquidity": liquidity_label(row.volume_sub),
        "as_of": iso_utc(row.ts),
        "model_version": row.model_version,
        "source_snapshot_id": row.source_snapshot_id,
        "inputs": components,
        "history_samples": history_samples,
        "sample_sizes": sample_sizes,
        "degraded_components": degraded_components,
        "missing_inputs": missing_inputs,
        "degraded": bool(degraded_components),
        "degradation_reason": details.get("degradation_reason"),
        "weights": details.get("weights", {}),
        "source": details.get("source", {}),
    }


@router.get("/monitor-feed")
def market_monitor_feed(
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(db_session_dependency),
    settings: Settings = Depends(settings_dependency),
) -> dict[str, Any]:
    items = build_feed(session, limit=limit, settings=settings)
    return {"count": len(items), "items": items}


@router.get("/intraday")
def market_intraday(
    symbols: str = Query(
        default="SH.000001,SZ.399001",
        description="逗号分隔的核心指数富途代码，最多 5 个。",
    ),
    client: FutuClient = Depends(futu_client_dependency),
) -> dict[str, list[dict[str, Any]]]:
    requested = list(
        dict.fromkeys(item.strip().upper() for item in symbols.split(",") if item.strip())
    )
    unsupported = [symbol for symbol in requested if symbol not in INDEX_INTRADAY_SYMBOLS]
    if not requested:
        raise HTTPException(status_code=422, detail="请至少指定一个指数代码。")
    if len(requested) > len(INDEX_INTRADAY_SYMBOLS) or unsupported:
        allowed = ", ".join(sorted(INDEX_INTRADAY_SYMBOLS))
        raise HTTPException(
            status_code=422,
            detail=f"仅支持最多 5 个核心指数；不支持 {unsupported}。可选：{allowed}",
        )
    try:
        return market_data.index_intraday(client, requested)
    except (FutuClientError, DataProviderError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"指数分时暂不可用，请确认 Futu OpenD 已启动并具备行情权限：{exc}",
        ) from exc
