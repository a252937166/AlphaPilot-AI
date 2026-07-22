from __future__ import annotations

from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from threading import RLock, Timer
from typing import Any, TypedDict

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from alphapilot.core.config import Settings
from alphapilot.data.baostock_provider import BaoStockMarketDataProvider
from alphapilot.data.base import (
    BarFrequency,
    DataProviderError,
    MarketDataProvider,
    PeriodicMarketDataProvider,
)
from alphapilot.data.futu_provider import FutuMarketDataProvider
from alphapilot.data.mock import MockMarketDataProvider
from alphapilot.db.models import DailyBar
from alphapilot.futu.client import FutuClient

BAR_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount"]
AUDITED_DAILY_BAR_SOURCES = frozenset(
    {"akshare", "baostock", "futu", "futu-close", "sina"}
)
_intraday_subscription_lock = RLock()
_intraday_cleanup_lock = RLock()
_intraday_cleanup_timers: dict[tuple[int, tuple[str, ...]], Timer] = {}
INTRADAY_MIN_SUBSCRIPTION_SECONDS = 65.0

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
    """Persist audited daily bars and repair rows previously polluted by test data."""
    if frame.empty:
        return 0
    normalized_source = source.strip().lower()
    if normalized_source not in AUDITED_DAILY_BAR_SOURCES:
        return 0
    dates = [pd.Timestamp(value).date() for value in frame["date"]]
    existing = {
        row.trade_date: row
        for row in session.scalars(
            select(DailyBar).where(
                DailyBar.symbol == symbol, DailyBar.trade_date.in_(dates)
            )
        )
    }
    changed = 0
    for record in frame.to_dict(orient="records"):
        trade_date = pd.Timestamp(record["date"]).date()
        values = {
            "open": _finite_float(record["open"], "open"),
            "high": _finite_float(record["high"], "high"),
            "low": _finite_float(record["low"], "low"),
            "close": _finite_float(record["close"], "close"),
            "volume": _finite_float_or_zero(record.get("volume")),
            "amount": _finite_float_or_zero(record.get("amount")),
        }
        current = existing.get(trade_date)
        if current is not None:
            current_source = str(current.source).strip().lower()
            if current_source not in AUDITED_DAILY_BAR_SOURCES:
                for field, value in values.items():
                    setattr(current, field, value)
                current.source = normalized_source
                changed += 1
            continue
        session.add(
            DailyBar(
                symbol=symbol,
                trade_date=trade_date,
                **values,
                source=normalized_source,
            )
        )
        changed += 1
    return changed


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


def _load_audited_resample_bars(
    session: Session,
    symbol: str,
    start: date,
    end: date,
) -> tuple[pd.DataFrame, list[str]]:
    """Load only cache rows whose provenance is safe for user-facing aggregation."""

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
        raise DataProviderError("本地日线缓存为空，无法降级聚合周期行情。")
    sources = sorted({str(row.source).strip().lower() for row in rows})
    rejected = sorted(set(sources).difference(AUDITED_DAILY_BAR_SOURCES))
    if rejected:
        raise DataProviderError(
            f"本地日线缓存含不可用于真实行情展示的来源 {rejected}，已拒绝周期聚合。"
        )
    return (
        pd.DataFrame(
            {
                "date": [pd.Timestamp(row.trade_date) for row in rows],
                "open": [row.open for row in rows],
                "high": [row.high for row in rows],
                "low": [row.low for row in rows],
                "close": [row.close for row in rows],
                "volume": [row.volume for row in rows],
                "amount": [row.amount for row in rows],
            }
        ),
        sources,
    )


def _resample_bars(frame: pd.DataFrame, frequency: BarFrequency) -> pd.DataFrame:
    """Aggregate real daily OHLCVA rows without inventing missing observations."""

    if frequency == "d" or frame.empty:
        return frame.copy()
    missing = [column for column in BAR_COLUMNS if column not in frame.columns]
    if missing:
        raise DataProviderError(f"daily bars cannot be resampled; missing columns: {missing}")
    working = frame[BAR_COLUMNS].copy()
    working["date"] = pd.to_datetime(working["date"], errors="coerce")
    for column in BAR_COLUMNS[1:]:
        working[column] = pd.to_numeric(working[column], errors="coerce")
    working = working.dropna(subset=["date", "open", "high", "low", "close"])
    if working.empty:
        raise DataProviderError("daily bars contain no valid rows to resample")
    working = working.sort_values("date", kind="stable")
    period_frequency = "W-FRI" if frequency == "w" else "M"
    working["_period"] = working["date"].dt.to_period(period_frequency)
    result = (
        working.groupby("_period", sort=True, observed=True)
        .agg(
            date=("date", "max"),
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            amount=("amount", "sum"),
        )
        .reset_index(drop=True)
    )
    return result[BAR_COLUMNS]


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
        try:
            cached, cache_sources = _load_audited_resample_bars(
                session,
                symbol,
                start,
                end,
            )
        except DataProviderError as cache_exc:
            raise DataProviderError(
                f"日线行情源不可用（{exc}）；缓存降级也被拒绝：{cache_exc}"
            ) from exc
        warnings.append(f"行情源不可用，已使用本地真实日线缓存：{exc}")
        return {
            "frame": cached,
            "source": f"cache-audited:{'+'.join(cache_sources)}",
            "warnings": warnings,
        }


def get_period_bars(
    session: Session,
    provider: MarketDataProvider,
    symbol: str,
    start: date,
    end: date,
    frequency: BarFrequency,
) -> BarsResult:
    """Fetch d/w/m bars while keeping the daily cache daily-only.

    Native weekly/monthly sources are preferred. A daily-only provider or a
    failed native request is resampled from real daily rows, with an explicit
    warning. Non-daily rows are never passed to ``save_bars``.
    """

    if frequency == "d":
        return get_bars_with_cache(session, provider, symbol, start, end)

    warnings: list[str] = []
    try:
        if isinstance(provider, PeriodicMarketDataProvider):
            frame = provider.get_bars(symbol, start, end, frequency)
            source = getattr(provider, "last_bars_source", None) or provider.name
        else:
            daily = provider.get_daily_bars(symbol, start, end)
            frame = _resample_bars(daily, frequency)
            source = provider.name
            warnings.append(
                f"{provider.name} 不支持原生{frequency}线，已由该来源日线聚合。"
            )
        chain_errors = getattr(provider, "last_errors", None)
        if chain_errors:
            warnings.extend(f"degraded source: {item}" for item in chain_errors)
        return {"frame": frame, "source": source, "warnings": warnings}
    except (DataProviderError, ValueError) as exc:
        try:
            cached, cache_sources = _load_audited_resample_bars(
                session,
                symbol,
                start,
                end,
            )
        except DataProviderError as cache_exc:
            raise DataProviderError(
                f"周期行情源不可用（{exc}）；缓存降级也被拒绝：{cache_exc}"
            ) from exc
        frame = _resample_bars(cached, frequency)
        warnings.append(f"周期行情源不可用，已由本地真实日线缓存聚合：{exc}")
        source = f"cache-resampled:{'+'.join(cache_sources)}"
        return {"frame": frame, "source": source, "warnings": warnings}


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


def index_intraday(
    client: FutuClient,
    symbols: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Subscribe to and return current-day minute series for audited symbols."""

    if not symbols:
        raise ValueError("intraday requires at least one symbol")
    with _intraday_subscription_lock:
        client.quote_call_raw(
            "subscribe",
            args=[symbols, ["RT_DATA"]],
            kwargs={"is_first_push": False},
        )
        try:
            series: dict[str, list[dict[str, Any]]] = {}
            required = {"time", "cur_price", "avg_price", "volume"}
            for symbol in symbols:
                payload = client.quote_call_raw("get_rt_data", args=[symbol])
                if not isinstance(payload, pd.DataFrame):
                    raise DataProviderError(f"Futu intraday payload is invalid: {symbol}")
                missing = sorted(required.difference(str(column) for column in payload.columns))
                if missing:
                    raise DataProviderError(
                        f"Futu intraday is missing fields for {symbol}: {missing}"
                    )
                points: list[dict[str, Any]] = []
                for raw_record in payload.to_dict(orient="records"):
                    record = {str(key): value for key, value in raw_record.items()}
                    if bool(record.get("is_blank", False)):
                        continue
                    price = _finite_or_none(record.get("cur_price"))
                    if price is None:
                        continue
                    points.append(
                        {
                            "time": str(record["time"]),
                            "price": price,
                            "avg_price": _finite_or_none(record.get("avg_price")),
                            "volume": _finite_or_none(record.get("volume")),
                        }
                    )
                series[symbol] = points
            return series
        finally:
            try:
                client.quote_call_raw(
                    "unsubscribe",
                    args=[symbols, ["RT_DATA"]],
                )
            except Exception:
                # OpenD rejects unsubscription during its one-minute minimum
                # holding window. Defer cleanup without blocking the request or
                # extending the lease on repeated page refreshes.
                _schedule_intraday_unsubscribe(client, symbols)


def _schedule_intraday_unsubscribe(client: FutuClient, symbols: list[str]) -> None:
    normalized = tuple(sorted(set(symbols)))
    key = (id(client), normalized)
    with _intraday_cleanup_lock:
        current = _intraday_cleanup_timers.get(key)
        if current is not None and current.is_alive():
            return

        def cleanup() -> None:
            try:
                client.quote_call_raw(
                    "unsubscribe",
                    args=[list(normalized), ["RT_DATA"]],
                )
            except Exception:
                pass
            finally:
                with _intraday_cleanup_lock:
                    _intraday_cleanup_timers.pop(key, None)

        timer = Timer(INTRADAY_MIN_SUBSCRIPTION_SECONDS, cleanup)
        timer.daemon = True
        _intraday_cleanup_timers[key] = timer
        timer.start()


def _iso(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return None


def _finite_or_none(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None
