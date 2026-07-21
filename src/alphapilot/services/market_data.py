from __future__ import annotations

from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from typing import Any, TypedDict

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from alphapilot.core.config import Settings
from alphapilot.data.baostock_provider import BaoStockMarketDataProvider
from alphapilot.data.base import DataProviderError, MarketDataProvider
from alphapilot.data.futu_provider import FutuMarketDataProvider
from alphapilot.data.mock import MockMarketDataProvider
from alphapilot.db.models import DailyBar

BAR_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount"]

# Core A-share benchmark indices for the market monitor page.
INDEX_SYMBOLS: list[dict[str, str]] = [
    {"symbol": "SH.000001", "name": "上证指数"},
    {"symbol": "SZ.399001", "name": "深证成指"},
    {"symbol": "SZ.399006", "name": "创业板指"},
    {"symbol": "SH.000300", "name": "沪深300"},
    {"symbol": "SH.000905", "name": "中证500"},
]


class BarsResult(TypedDict):
    frame: pd.DataFrame
    source: str
    warnings: list[str]


def _finite_float(value: Any, field: str) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"daily bar {field} must be finite")
    return number


def _finite_float_or_zero(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if isfinite(number) else 0.0


def save_bars(session: Session, symbol: str, frame: pd.DataFrame, source: str) -> int:
    """Insert missing daily bars; existing (symbol, date) rows are left untouched."""
    if frame.empty:
        return 0
    dates = [pd.Timestamp(value).date() for value in frame["date"]]
    existing = set(
        session.scalars(
            select(DailyBar.trade_date).where(
                DailyBar.symbol == symbol, DailyBar.trade_date.in_(dates)
            )
        )
    )
    inserted = 0
    for record in frame.to_dict(orient="records"):
        trade_date = pd.Timestamp(record["date"]).date()
        if trade_date in existing:
            continue
        session.add(
            DailyBar(
                symbol=symbol,
                trade_date=trade_date,
                open=_finite_float(record["open"], "open"),
                high=_finite_float(record["high"], "high"),
                low=_finite_float(record["low"], "low"),
                close=_finite_float(record["close"], "close"),
                volume=_finite_float_or_zero(record.get("volume")),
                amount=_finite_float_or_zero(record.get("amount")),
                source=source,
            )
        )
        inserted += 1
    return inserted


def latest_trade_date(session: Session) -> date:
    """Return the newest cached benchmark date or the latest weekday fallback."""

    cached = session.scalar(
        select(func.max(DailyBar.trade_date)).where(DailyBar.symbol == "SH.000001")
    )
    if isinstance(cached, date):
        return cached
    candidate = date.today()
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def load_bars(session: Session, symbol: str, start: date, end: date) -> pd.DataFrame:
    rows = session.scalars(
        select(DailyBar)
        .where(
            DailyBar.symbol == symbol,
            DailyBar.trade_date >= start,
            DailyBar.trade_date <= end,
        )
        .order_by(DailyBar.trade_date)
    ).all()
    if not rows:
        return pd.DataFrame(columns=BAR_COLUMNS)
    return pd.DataFrame(
        {
            "date": [pd.Timestamp(row.trade_date) for row in rows],
            "open": [row.open for row in rows],
            "high": [row.high for row in rows],
            "low": [row.low for row in rows],
            "close": [row.close for row in rows],
            "volume": [row.volume for row in rows],
            "amount": [row.amount for row in rows],
        }
    )


def get_bars_with_cache(
    session: Session,
    provider: MarketDataProvider,
    symbol: str,
    start: date,
    end: date,
) -> BarsResult:
    """Fetch bars from the provider chain; fall back to the local cache when all fail."""
    warnings: list[str] = []
    try:
        frame = provider.get_daily_bars(symbol, start, end)
        source = getattr(provider, "last_bars_source", None) or provider.name
        save_bars(session, symbol, frame, source)
        chain_errors = getattr(provider, "last_errors", None)
        if chain_errors:
            warnings.extend(f"degraded source: {item}" for item in chain_errors)
        return {"frame": frame, "source": source, "warnings": warnings}
    except (DataProviderError, ValueError) as exc:
        cached = load_bars(session, symbol, start, end)
        if cached.empty:
            raise
        warnings.append(f"providers unavailable, serving cached bars: {exc}")
        return {"frame": cached, "source": "cache", "warnings": warnings}


def build_index_provider(settings: Settings) -> MarketDataProvider:
    """Index history must avoid AKShare: its stock endpoint would silently
    return share data for index-looking codes such as 000001."""
    from alphapilot.data.router import FailoverMarketDataProvider

    chain: list[MarketDataProvider] = [BaoStockMarketDataProvider()]
    with suppress(Exception):  # the futu extra is optional
        chain.append(FutuMarketDataProvider(settings))
    if settings.default_data_provider == "mock":
        chain = [MockMarketDataProvider()]
    return FailoverMarketDataProvider(chain, chain)


def index_history(
    session: Session,
    settings: Settings,
    days: int = 120,
) -> dict[str, pd.DataFrame]:
    provider = build_index_provider(settings)
    end = date.today()
    start = end - timedelta(days=int(days * 1.7))
    series: dict[str, pd.DataFrame] = {}
    for entry in INDEX_SYMBOLS:
        try:
            result = get_bars_with_cache(session, provider, entry["symbol"], start, end)
            series[entry["symbol"]] = result["frame"]
        except (DataProviderError, ValueError):
            continue
    return series


def index_quotes(settings: Settings) -> list[dict[str, object]]:
    """Real-time index snapshot via Futu; degrade to empty list without it."""
    try:
        provider = FutuMarketDataProvider(settings)
        frame = provider.get_snapshot([entry["symbol"] for entry in INDEX_SYMBOLS])
    except Exception:
        return []
    names = {entry["symbol"]: entry["name"] for entry in INDEX_SYMBOLS}
    quotes: list[dict[str, object]] = []
    for record in frame.to_dict(orient="records"):
        symbol = str(record.get("symbol"))
        quotes.append(
            {
                "symbol": symbol,
                "name": names.get(symbol, symbol),
                "last": record.get("last"),
                "change_pct": record.get("change_pct"),
                "amount": record.get("amount"),
                "as_of": _iso(record.get("as_of")),
            }
        )
    return quotes


def _iso(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return None
