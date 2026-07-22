from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from math import copysign, isfinite
from typing import Any, Literal, TypedDict
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.alerts.service import AlertService
from alphapilot.core.config import get_settings
from alphapilot.core.timeutil import iso_utc
from alphapilot.data.base import DataProviderError, MarketDataProvider
from alphapilot.db.models import (
    AlertRecord,
    CalendarEvent,
    DailyBar,
    DomainEvent,
    ForecastSnapshot,
    Security,
    ThesisTransition,
    WatchlistItem,
)
from alphapilot.domain.models import StockForecast
from alphapilot.engines.thesis_drift import THESIS_STATES, evaluate
from alphapilot.prediction.baseline import BaselineForecastEngine
from alphapilot.services.market_data import get_bars_with_cache
from alphapilot.services.notifications import push_alert

DEFAULT_WATCHLIST: list[dict[str, str]] = [
    {"symbol": "600519", "display_name": "贵州茅台", "group_name": "core"},
    {"symbol": "300750", "display_name": "宁德时代", "group_name": "core"},
    {"symbol": "002594", "display_name": "比亚迪", "group_name": "core"},
    {"symbol": "600000", "display_name": "浦发银行", "group_name": "watch"},
    {"symbol": "000333", "display_name": "美的集团", "group_name": "watch"},
]
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
RECENT_EVENT_LIMIT = 3
CALENDAR_EVENT_TYPES = frozenset(
    {
        "dividend",
        "earnings_preview",
        "earnings_report",
        "meeting",
        "unlock",
    }
)


class RecentEventPayload(TypedDict):
    id: int
    event_type: str
    category: Literal["announcement", "calendar", "capital"]
    title: str
    summary: str | None
    source_ref: str | None
    direction: float | None
    strength: float | None
    occurred_at: str


def normalize_symbol(symbol: str) -> str:
    upper = symbol.upper().replace(" ", "")
    if "." in upper:
        return upper.split(".", 1)[1]
    return upper


def seed_default_watchlist(session: Session) -> None:
    existing = session.scalars(select(WatchlistItem.symbol)).all()
    if existing:
        return
    for entry in DEFAULT_WATCHLIST:
        session.add(
            WatchlistItem(
                symbol=entry["symbol"],
                display_name=entry["display_name"],
                group_name=entry["group_name"],
                thesis="示例追踪标的，请替换为自己的投资逻辑。",
            )
        )


def list_items(session: Session) -> list[WatchlistItem]:
    return list(session.scalars(select(WatchlistItem).order_by(WatchlistItem.created_at)).all())


def upsert_item(session: Session, payload: dict[str, Any]) -> WatchlistItem:
    symbol = normalize_symbol(str(payload["symbol"]))
    item = session.get(WatchlistItem, symbol)
    if item is None:
        item = WatchlistItem(symbol=symbol)
        session.add(item)
    for field in (
        "group_name",
        "display_name",
        "cost_price",
        "quantity",
        "thesis",
        "catalysts",
        "risks",
        "invalidation_rules",
        "initial_confidence",
    ):
        if field in payload and payload[field] is not None:
            setattr(item, field, payload[field])
    item.updated_at = datetime.now(UTC)
    return item


def remove_item(session: Session, symbol: str) -> bool:
    item = session.get(WatchlistItem, normalize_symbol(symbol))
    if item is None:
        return False
    session.delete(item)
    return True


def forecast_for_symbol(
    session: Session,
    provider: MarketDataProvider,
    symbol: str,
    lookback_days: int = 220,
) -> StockForecast:
    end = date.today()
    start = end - timedelta(days=int(lookback_days * 1.7))
    result = get_bars_with_cache(session, provider, symbol, start, end)
    forecast = BaselineForecastEngine().forecast(symbol, result["frame"], result["source"])
    forecast.warnings.extend(result["warnings"])
    return forecast


def persist_forecast(session: Session, forecast: StockForecast) -> ForecastSnapshot:
    snapshot = ForecastSnapshot(
        symbol=forecast.symbol,
        as_of=forecast.as_of,
        provider=forecast.provider,
        model_version=forecast.model_version,
        horizons={key: value.model_dump(mode="json") for key, value in forecast.horizons.items()},
        features=forecast.features,
    )
    session.add(snapshot)
    return snapshot


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _snapshot_last_prices(
    provider: MarketDataProvider,
    symbols: list[str],
) -> dict[str, float]:
    if not symbols:
        return {}
    try:
        frame = provider.get_snapshot(symbols)
        raw_records = frame.to_dict(orient="records")
    except Exception:
        return {}
    prices: dict[str, float] = {}
    for raw_record in raw_records:
        record = {str(key): value for key, value in raw_record.items()}
        symbol = normalize_symbol(str(record.get("symbol", "")))
        last = _finite_float(record.get("last"))
        if len(symbol) == 6 and last is not None and last > 0:
            prices[symbol] = last
    return prices


def _latest_close(
    session: Session,
    symbol: str,
    cutoff: date,
) -> float | None:
    value = session.scalar(
        select(DailyBar.close)
        .where(
            DailyBar.symbol == symbol,
            DailyBar.trade_date <= cutoff,
            DailyBar.close > 0,
        )
        .order_by(DailyBar.trade_date.desc(), DailyBar.id.desc())
        .limit(1)
    )
    close = _finite_float(value)
    return close if close is not None and close > 0 else None


def _alert_targets_and_notional(
    forecast: StockForecast,
    *,
    last_price: float | None,
    position_change: float,
    demo_equity: float,
) -> tuple[float | None, float | None, float | None, list[str]]:
    warnings: list[str] = []
    horizon = forecast.horizons.get("20d")
    q10 = _finite_float(horizon.q10) if horizon is not None else None
    q90 = _finite_float(horizon.q90) if horizon is not None else None
    valid_interval = bool(
        last_price is not None
        and last_price > 0
        and q10 is not None
        and q90 is not None
        and q10 > -1.0
        and q10 < q90
    )
    target_low: float | None = None
    target_high: float | None = None
    if valid_interval:
        assert last_price is not None and q10 is not None and q90 is not None
        low = last_price * (1.0 + q10)
        high = last_price * (1.0 + q90)
        if isfinite(low) and isfinite(high) and 0 < low < high:
            target_low, target_high = low, high
    if target_low is None or target_high is None:
        warnings.append("缺少有效现价或20日分位区间，目标价暂不可用。")

    if position_change == 0:
        suggested_notional: float | None = 0.0
    else:
        volatility = _finite_float(forecast.features.get("volatility_20d"))
        if volatility is None or volatility < 0:
            suggested_notional = None
            warnings.append("缺少有效20日波动率，建议金额暂不可用。")
        else:
            scale = 1.0 if volatility == 0 else min(1.0, 0.3 / volatility)
            magnitude = demo_equity * abs(position_change) * scale
            suggested_notional = copysign(magnitude, position_change)
    return target_low, target_high, suggested_notional, warnings


def refresh_alerts(
    session: Session,
    provider: MarketDataProvider,
    *,
    symbols: list[str] | None = None,
) -> list[AlertRecord]:
    """Recompute forecasts for tracked symbols, persist forecasts and alerts."""
    items = list_items(session)
    targets = [item.symbol for item in items] if symbols is None else symbols
    snapshot_prices = _snapshot_last_prices(provider, targets)
    demo_equity = get_settings().demo_equity
    alert_service = AlertService()
    created: list[AlertRecord] = []
    for symbol in targets:
        normalized_symbol = normalize_symbol(symbol)
        try:
            forecast = forecast_for_symbol(session, provider, symbol)
        except (DataProviderError, ValueError):
            evaluate(session, symbol, event_only=True, created_alerts=created)
            continue
        persist_forecast(session, forecast)
        alert = alert_service.evaluate(forecast)
        last_price = snapshot_prices.get(normalized_symbol)
        used_close_fallback = last_price is None
        if used_close_fallback:
            last_price = _latest_close(
                session,
                normalized_symbol,
                forecast.as_of.date(),
            )
        target_low, target_high, suggested_notional, enrichment_warnings = (
            _alert_targets_and_notional(
                forecast,
                last_price=last_price,
                position_change=alert.suggested_position_change,
                demo_equity=demo_equity,
            )
        )
        if used_close_fallback and last_price is not None:
            enrichment_warnings.append("实时快照不可用，目标区间按最新日线收盘价计算。")
        record = AlertRecord(
            symbol=alert.symbol,
            action=alert.action.value,
            urgency=alert.urgency.value,
            confidence=alert.confidence,
            suggested_position_change=alert.suggested_position_change,
            target_low=target_low,
            target_high=target_high,
            suggested_notional=suggested_notional,
            reasons=[*alert.reasons, *enrichment_warnings],
            invalidation=alert.invalidation,
            model_version=alert.model_version,
            as_of=alert.as_of,
            expires_at=alert.expires_at,
        )
        session.add(record)
        created.append(record)
        session.flush()
        push_alert(session, record)
        evaluate(session, symbol, created_alerts=created)
    return created


def latest_alert_by_symbol(session: Session) -> dict[str, AlertRecord]:
    records = session.scalars(select(AlertRecord).order_by(AlertRecord.created_at)).all()
    latest: dict[str, AlertRecord] = {}
    for record in records:
        latest[record.symbol] = record
    return latest


def _event_category(
    event_type: str,
) -> Literal["announcement", "calendar", "capital"] | None:
    if event_type == "disclosure":
        return "announcement"
    if event_type == "capital_anomaly":
        return "capital"
    if event_type in CALENDAR_EVENT_TYPES:
        return "calendar"
    return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _recent_events_by_symbol(
    session: Session,
    symbols: list[str],
) -> dict[str, list[RecentEventPayload]]:
    """Return the newest three product-relevant events per symbol.

    Normalized announcements and capital anomalies live in ``events`` while the
    production calendar sync persists scheduled events in ``calendar_events``.
    Both sources are queried in batches and merged before the per-symbol limit is
    applied so the UI never fabricates calendar events or lets unrelated domain
    events displace the three supported product categories.
    """

    grouped: dict[str, list[RecentEventPayload]] = {symbol: [] for symbol in symbols}
    if not symbols:
        return grouped
    ranked_domain_events = (
        select(
            DomainEvent.id.label("event_id"),
            func.row_number()
            .over(
                partition_by=DomainEvent.symbol,
                order_by=(DomainEvent.occurred_at.desc(), DomainEvent.id.desc()),
            )
            .label("event_rank"),
        )
        .where(
            DomainEvent.symbol.in_(symbols),
            DomainEvent.event_type.in_({"disclosure", "capital_anomaly", *CALENDAR_EVENT_TYPES}),
        )
        .subquery()
    )
    domain_events = session.scalars(
        select(DomainEvent)
        .join(ranked_domain_events, ranked_domain_events.c.event_id == DomainEvent.id)
        .where(ranked_domain_events.c.event_rank <= RECENT_EVENT_LIMIT)
        .order_by(
            DomainEvent.symbol,
            DomainEvent.occurred_at.desc(),
            DomainEvent.id.desc(),
        )
    ).all()
    ranked_calendar_events = (
        select(
            CalendarEvent.id.label("event_id"),
            func.row_number()
            .over(
                partition_by=CalendarEvent.symbol,
                order_by=(CalendarEvent.event_date.desc(), CalendarEvent.id.desc()),
            )
            .label("event_rank"),
        )
        .where(CalendarEvent.symbol.in_(symbols))
        .subquery()
    )
    calendar_events = session.scalars(
        select(CalendarEvent)
        .join(ranked_calendar_events, ranked_calendar_events.c.event_id == CalendarEvent.id)
        .where(ranked_calendar_events.c.event_rank <= RECENT_EVENT_LIMIT)
        .order_by(
            CalendarEvent.symbol,
            CalendarEvent.event_date.desc(),
            CalendarEvent.id.desc(),
        )
    ).all()

    candidates: dict[str, list[tuple[datetime, int, RecentEventPayload]]] = {
        symbol: [] for symbol in symbols
    }
    for domain_event in domain_events:
        if domain_event.symbol is None or domain_event.symbol not in grouped:
            continue
        category = _event_category(domain_event.event_type)
        if category is None:
            continue
        event_time = _as_utc(domain_event.occurred_at)
        occurred_at = iso_utc(event_time)
        if occurred_at is None:
            continue
        candidates[domain_event.symbol].append(
            (
                event_time,
                domain_event.id,
                {
                    "id": domain_event.id,
                    "event_type": domain_event.event_type,
                    "category": category,
                    "title": domain_event.title,
                    "summary": domain_event.summary,
                    "source_ref": domain_event.source_ref,
                    "direction": domain_event.direction,
                    "strength": domain_event.strength,
                    "occurred_at": occurred_at,
                },
            )
        )

    for calendar_event in calendar_events:
        if calendar_event.symbol not in grouped:
            continue
        event_time = datetime.combine(
            calendar_event.event_date,
            time.min,
            tzinfo=MARKET_TIMEZONE,
        ).astimezone(UTC)
        occurred_at = iso_utc(event_time)
        if occurred_at is None:
            continue
        candidates[calendar_event.symbol].append(
            (
                event_time,
                calendar_event.id,
                {
                    "id": calendar_event.id,
                    "event_type": calendar_event.event_type,
                    "category": "calendar",
                    "title": calendar_event.title,
                    "summary": None,
                    "source_ref": f"calendar:{calendar_event.id}",
                    "direction": None,
                    "strength": None,
                    "occurred_at": occurred_at,
                },
            )
        )

    for symbol, rows in candidates.items():
        rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
        grouped[symbol] = [row[2] for row in rows[:RECENT_EVENT_LIMIT]]
    return grouped


def _industries_by_symbol(session: Session, symbols: list[str]) -> dict[str, str]:
    securities = session.scalars(select(Security).where(Security.symbol.in_(symbols))).all()
    industries: dict[str, str] = {}
    for security in securities:
        industry = next(
            (
                value.strip()
                for value in (
                    security.industry_csrc,
                    security.industry_futu,
                    security.industry,
                )
                if value and value.strip()
            ),
            "未分类",
        )
        industries[security.symbol] = industry
    return industries


def watchlist_summary(
    session: Session,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return current counts and transitions from seven Shanghai calendar dates."""

    counts = {state: 0 for state in THESIS_STATES}
    for state in session.scalars(select(WatchlistItem.thesis_state)).all():
        if state in counts:
            counts[state] += 1
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    current_time = current_time.astimezone(UTC)
    local_today = current_time.astimezone(MARKET_TIMEZONE).date()
    local_start = datetime.combine(
        local_today - timedelta(days=6),
        datetime.min.time(),
        tzinfo=MARKET_TIMEZONE,
    )
    utc_start = local_start.astimezone(UTC)
    transitions = session.scalars(
        select(ThesisTransition)
        .where(
            ThesisTransition.created_at >= utc_start,
            ThesisTransition.created_at <= current_time,
        )
        .order_by(ThesisTransition.created_at, ThesisTransition.id)
    ).all()
    daily_counts = {
        local_today - timedelta(days=offset): {state: 0 for state in THESIS_STATES}
        for offset in range(6, -1, -1)
    }
    for transition in transitions:
        created_at = transition.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        local_date = created_at.astimezone(MARKET_TIMEZONE).date()
        if local_date in daily_counts and transition.to_state in THESIS_STATES:
            daily_counts[local_date][transition.to_state] += 1
    return {
        **counts,
        "transitions_7d": [
            {
                "date": local_date.isoformat(),
                **state_counts,
            }
            for local_date, state_counts in daily_counts.items()
        ],
    }


def tracked_overview(
    session: Session,
    provider: MarketDataProvider,
) -> list[dict[str, Any]]:
    """Watchlist enriched with quotes, latest persisted forecast and alert."""
    items = list_items(session)
    if not items:
        return []
    quotes: dict[str, dict[str, Any]] = {}
    try:
        snapshot = provider.get_snapshot([item.symbol for item in items])
        for record in snapshot.to_dict(orient="records"):
            code = str(record.get("symbol", ""))
            quotes[code.split(".")[-1]] = {str(key): value for key, value in record.items()}
    except (DataProviderError, Exception):
        quotes = {}

    symbols = [item.symbol for item in items]
    alerts = latest_alert_by_symbol(session)
    recent_events = _recent_events_by_symbol(session, symbols)
    industries = _industries_by_symbol(session, symbols)
    rows: list[dict[str, Any]] = []
    for item in items:
        forecast = session.scalars(
            select(ForecastSnapshot)
            .where(ForecastSnapshot.symbol == item.symbol)
            .order_by(ForecastSnapshot.as_of.desc())
            .limit(1)
        ).first()
        quote = quotes.get(item.symbol, {})
        alert = alerts.get(item.symbol)
        horizon_20d = (forecast.horizons if forecast else {}).get("20d", {})
        last_price = quote.get("last")
        cost = item.cost_price
        pnl_pct = (
            (float(last_price) / float(cost) - 1) * 100 if last_price is not None and cost else None
        )
        rows.append(
            {
                "symbol": item.symbol,
                "display_name": item.display_name,
                "group_name": item.group_name,
                "industry": industries.get(item.symbol, "未分类"),
                "cost_price": item.cost_price,
                "quantity": item.quantity,
                "thesis": item.thesis,
                "thesis_state": item.thesis_state,
                "last": last_price,
                "change_pct": quote.get("change_pct"),
                "pnl_pct": pnl_pct,
                "p_up_20d": horizon_20d.get("p_up"),
                "expected_return_20d": horizon_20d.get("expected_return"),
                "confidence_20d": horizon_20d.get("confidence"),
                "forecast_as_of": iso_utc(forecast.as_of) if forecast else None,
                "alert_action": alert.action if alert else None,
                "alert_urgency": alert.urgency if alert else None,
                "alert_confidence": alert.confidence if alert else None,
                "recent_events": recent_events[item.symbol],
            }
        )
    return rows
