from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.alerts.service import AlertService
from alphapilot.core.timeutil import iso_utc
from alphapilot.data.base import DataProviderError, MarketDataProvider
from alphapilot.db.models import AlertRecord, ForecastSnapshot, WatchlistItem
from alphapilot.domain.models import StockForecast
from alphapilot.prediction.baseline import BaselineForecastEngine
from alphapilot.services.market_data import get_bars_with_cache

DEFAULT_WATCHLIST: list[dict[str, str]] = [
    {"symbol": "600519", "display_name": "贵州茅台", "group_name": "core"},
    {"symbol": "300750", "display_name": "宁德时代", "group_name": "core"},
    {"symbol": "002594", "display_name": "比亚迪", "group_name": "core"},
    {"symbol": "600000", "display_name": "浦发银行", "group_name": "watch"},
    {"symbol": "000333", "display_name": "美的集团", "group_name": "watch"},
]


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
    return list(
        session.scalars(select(WatchlistItem).order_by(WatchlistItem.created_at)).all()
    )


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
        "thesis_state",
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
        horizons={
            key: value.model_dump(mode="json") for key, value in forecast.horizons.items()
        },
        features=forecast.features,
    )
    session.add(snapshot)
    return snapshot


def refresh_alerts(
    session: Session,
    provider: MarketDataProvider,
    *,
    symbols: list[str] | None = None,
) -> list[AlertRecord]:
    """Recompute forecasts for tracked symbols, persist forecasts and alerts."""
    items = list_items(session)
    targets = symbols or [item.symbol for item in items]
    alert_service = AlertService()
    created: list[AlertRecord] = []
    for symbol in targets:
        try:
            forecast = forecast_for_symbol(session, provider, symbol)
        except (DataProviderError, ValueError):
            continue
        persist_forecast(session, forecast)
        alert = alert_service.evaluate(forecast)
        record = AlertRecord(
            symbol=alert.symbol,
            action=alert.action.value,
            urgency=alert.urgency.value,
            confidence=alert.confidence,
            suggested_position_change=alert.suggested_position_change,
            reasons=list(alert.reasons),
            invalidation=alert.invalidation,
            model_version=alert.model_version,
            as_of=alert.as_of,
            expires_at=alert.expires_at,
        )
        session.add(record)
        created.append(record)
    return created


def latest_alert_by_symbol(session: Session) -> dict[str, AlertRecord]:
    records = session.scalars(select(AlertRecord).order_by(AlertRecord.created_at)).all()
    latest: dict[str, AlertRecord] = {}
    for record in records:
        latest[record.symbol] = record
    return latest


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

    alerts = latest_alert_by_symbol(session)
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
            (float(last_price) / float(cost) - 1) * 100
            if last_price is not None and cost
            else None
        )
        rows.append(
            {
                "symbol": item.symbol,
                "display_name": item.display_name,
                "group_name": item.group_name,
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
            }
        )
    return rows
