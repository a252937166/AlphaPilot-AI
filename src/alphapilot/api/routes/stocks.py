from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from math import ceil, isfinite
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.alerts.service import AlertService
from alphapilot.api.dependencies import (
    cninfo_client_dependency,
    db_session_dependency,
    get_provider,
)
from alphapilot.cninfo.client import CninfoClient, CninfoError
from alphapilot.core.timeutil import iso_utc
from alphapilot.data.base import BarFrequency, DataProviderError
from alphapilot.db.models import AlertRecord, CalendarEvent, DailyBar
from alphapilot.domain.models import StockAlert, StockForecast
from alphapilot.prediction.baseline import BaselineForecastEngine
from alphapilot.services import disclosures as disclosure_service
from alphapilot.services import insight as insight_service
from alphapilot.services import stock_scores as stock_score_service
from alphapilot.services.alert_outcomes import BUY_ACTIONS, SELL_ACTIONS
from alphapilot.services.alert_provenance import alert_provenance
from alphapilot.services.market_data import (
    AUDITED_DAILY_BAR_SOURCES,
    get_bars_with_cache,
    get_period_bars,
)
from alphapilot.services.watchlist import normalize_symbol

router = APIRouter(prefix="/v1/stocks", tags=["stocks"])
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _finite_or_none(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _timestamp_payload(value: object) -> str | None:
    if not isinstance(value, datetime):
        return None
    return iso_utc(value)


def _market_date(value: datetime) -> date:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return aware.astimezone(MARKET_TIMEZONE).date()


def _future_datetime(value: datetime | None) -> bool:
    if value is None:
        return False
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return aware.astimezone(UTC) > datetime.now(UTC)


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
    freq: BarFrequency = Query(default="d"),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    selected = get_provider(provider)
    end = date.today()
    lookback_multiplier = {"d": 1.7, "w": 8.0, "m": 35.0}[freq]
    start = end - timedelta(days=ceil(days * lookback_multiplier))
    try:
        result = get_period_bars(session, selected, symbol, start, end, freq)
    except (DataProviderError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    frame = result["frame"].tail(days)
    return {
        "symbol": symbol,
        "frequency": freq,
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


@router.get("/{symbol}/signals")
def stock_signals(
    symbol: str,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    """Return directional historical alerts that can be rendered as B/S markers."""

    code = normalize_symbol(symbol)
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(status_code=422, detail="股票代码必须是 6 位数字。")
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=422, detail="信号开始日期不能晚于结束日期。")

    market = "SH" if code.startswith(("5", "6", "9")) else "SZ"
    symbol_variants = {code, f"{market}.{code}"}
    directional_actions = BUY_ACTIONS | SELL_ACTIONS
    records = session.scalars(
        select(AlertRecord)
        .where(
            AlertRecord.symbol.in_(symbol_variants),
            AlertRecord.action.in_(directional_actions),
        )
        .order_by(AlertRecord.created_at, AlertRecord.id)
    ).all()

    selected_records: list[tuple[AlertRecord, datetime, date, int, str, bool]] = []
    excluded_count = 0
    for record in records:
        effective_at = record.as_of or record.created_at
        trade_date = _market_date(effective_at)
        if start is not None and trade_date < start:
            continue
        if end is not None and trade_date > end:
            continue
        provenance = alert_provenance(session, record)
        if not provenance.verified or provenance.forecast_snapshot_id is None:
            excluded_count += 1
            continue
        selected_records.append(
            (
                record,
                effective_at,
                trade_date,
                provenance.forecast_snapshot_id,
                provenance.provider or "unknown",
                _future_datetime(record.expires_at),
            )
        )
    selected_records.sort(key=lambda item: (item[2], item[1], item[0].id))

    trade_dates = sorted({item[2] for item in selected_records})
    close_evidence: dict[date, tuple[float | None, str | None]] = {}
    if trade_dates:
        close_rows = session.execute(
            select(DailyBar.trade_date, DailyBar.close, DailyBar.source).where(
                DailyBar.symbol == code,
                DailyBar.trade_date.in_(trade_dates),
            )
        ).all()
        for trade_date, value, raw_source in close_rows:
            source = str(raw_source).strip().lower() if raw_source is not None else None
            close = _finite_or_none(value)
            if (
                source not in AUDITED_DAILY_BAR_SOURCES
                or close is None
                or close <= 0
            ):
                close = None
            close_evidence[trade_date] = (close, source)

    signals = [
        {
            "id": record.id,
            "symbol": code,
            "action": record.action,
            "marker": "B" if record.action in BUY_ACTIONS else "S",
            "confidence": record.confidence,
            "target_low": record.target_low,
            "target_high": record.target_high,
            "suggested_notional": record.suggested_notional,
            "reasons": record.reasons,
            "invalidation": record.invalidation,
            "model_version": record.model_version,
            "expires_at": iso_utc(record.expires_at),
            "forecast_snapshot_id": forecast_snapshot_id,
            "forecast_provider": forecast_provider,
            "trade_eligible": trade_eligible,
            "trade_date": trade_date.isoformat(),
            "close": close_evidence.get(trade_date, (None, None))[0],
            "close_source": close_evidence.get(trade_date, (None, None))[1],
            "as_of": iso_utc(record.as_of),
            "created_at": iso_utc(record.created_at),
        }
        for (
            record,
            _effective_at,
            trade_date,
            forecast_snapshot_id,
            forecast_provider,
            trade_eligible,
        ) in selected_records
    ]
    return {
        "symbol": code,
        "from": start.isoformat() if start is not None else None,
        "to": end.isoformat() if end is not None else None,
        "count": len(signals),
        "excluded_count": excluded_count,
        "warnings": (
            [f"{excluded_count} 条方向提醒因行情来源不可审计而未展示。"]
            if excluded_count
            else []
        ),
        "signals": signals,
    }


@router.get("/{symbol}/calendar")
def stock_calendar(
    symbol: str,
    days: int = Query(default=90, ge=1, le=730),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    code = normalize_symbol(symbol)
    today = date.today()
    start = today - timedelta(days=days)
    end = today + timedelta(days=days)
    rows = session.scalars(
        select(CalendarEvent)
        .where(
            CalendarEvent.symbol == code,
            CalendarEvent.event_date >= start,
            CalendarEvent.event_date <= end,
        )
        .order_by(CalendarEvent.event_date, CalendarEvent.id)
    ).all()
    return {
        "symbol": code,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "days": days,
        "events": [
            {
                "id": row.id,
                "symbol": row.symbol,
                "event_type": row.event_type,
                "event_date": row.event_date.isoformat(),
                "title": row.title,
                "payload": row.payload,
                "source": row.source,
                "available_time": iso_utc(row.available_time),
            }
            for row in rows
        ],
    }


@router.get("/{symbol}/score")
def stock_score(
    symbol: str,
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    code = normalize_symbol(symbol)
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(status_code=422, detail="股票代码必须是 6 位数字。")
    payload = stock_score_service.latest_score_payload(session, code)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail=f"暂无 {code} 的最新五维评分，请先运行 compute_factors。",
        )
    return payload


@router.get("/{symbol}/insight")
def stock_insight(
    symbol: str,
    force: bool = Query(default=False),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    try:
        insight = insight_service.get_or_build(session, symbol, force=force)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return insight_service.insight_payload(insight)


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
                "last": _finite_or_none(record.get("last")),
                "change_pct": _finite_or_none(record.get("change_pct")),
                "open": _finite_or_none(record.get("open", record.get("open_price"))),
                "high": _finite_or_none(record.get("high", record.get("high_price"))),
                "low": _finite_or_none(record.get("low", record.get("low_price"))),
                "volume": _finite_or_none(record.get("volume")),
                "amount": _finite_or_none(record.get("amount", record.get("turnover"))),
                "turnover_rate": _finite_or_none(record.get("turnover_rate")),
                "pe_ttm": _finite_or_none(
                    record.get("pe_ttm", record.get("pe_ttm_ratio", record.get("pe_ratio")))
                ),
                "market_cap": _finite_or_none(
                    record.get("market_cap", record.get("total_market_val"))
                ),
                "float_cap": _finite_or_none(
                    record.get("float_cap", record.get("circular_market_val"))
                ),
                "pb": _finite_or_none(record.get("pb", record.get("pb_ratio"))),
                "as_of": _timestamp_payload(record.get("as_of")),
                "fundamentals_as_of": None,
                "source": getattr(selected, "last_snapshot_source", None) or selected.name,
                "ohlc_source": "snapshot",
                "ohlc_trade_date": None,
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
    if quote is not None and security is not None:
        for field in ("turnover_rate", "pe_ttm", "market_cap", "float_cap", "pb"):
            if quote[field] is None:
                quote[field] = _finite_or_none(security.get(field))
        quote["fundamentals_as_of"] = security.get("snapshot_at")
    if quote is not None and any(quote[field] is None for field in ("open", "high", "low")):
        quote_as_of = quote.get("as_of")
        quote_trade_date: date | None = None
        if isinstance(quote_as_of, str):
            try:
                quote_trade_date = _market_date(datetime.fromisoformat(quote_as_of))
            except ValueError:
                quote_trade_date = None
        latest_bar = None
        if quote_trade_date is not None:
            latest_bar = session.scalars(
                select(DailyBar)
                .where(
                    DailyBar.symbol == code,
                    DailyBar.trade_date == quote_trade_date,
                    DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
                )
                .order_by(DailyBar.id.desc())
                .limit(1)
            ).first()
        filled_from_daily = False
        if latest_bar is not None:
            for field in ("open", "high", "low"):
                if quote[field] is None:
                    fallback = _finite_or_none(getattr(latest_bar, field))
                    if fallback is not None:
                        quote[field] = fallback
                        filled_from_daily = True
            if filled_from_daily:
                quote["ohlc_source"] = f"daily-bar:{latest_bar.source}"
                quote["ohlc_trade_date"] = latest_bar.trade_date.isoformat()
        if not filled_from_daily and any(
            quote[field] is None for field in ("open", "high", "low")
        ):
            quote["ohlc_source"] = "unavailable"
    disclosures = disclosure_service.list_disclosures(session, code, limit=10)
    score = stock_score_service.latest_score_payload(session, code)

    return {
        "symbol": code,
        "security": security,
        "quote": quote,
        "forecast": forecast.model_dump(mode="json"),
        "alert": alert.model_dump(mode="json"),
        "disclosures": disclosures,
        "score": score,
        "score_error": (
            None if score is not None else "暂无最新五维评分，请先运行 compute_factors。"
        ),
    }
