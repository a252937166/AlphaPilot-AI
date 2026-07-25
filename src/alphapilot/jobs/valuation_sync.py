from __future__ import annotations

import fcntl
import logging
import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from random import uniform
from time import monotonic, sleep
from typing import Any, TextIO
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from alphapilot.data.base import DataProviderError
from alphapilot.data.provenance import AUDITED_DAILY_BAR_SOURCES
from alphapilot.db.engine import get_session
from alphapilot.db.models import DailyBar, Security, ValuationDaily
from alphapilot.jobs.registry import JobExecutionError, JobSpec, register

logger = logging.getLogger(__name__)

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
DEFAULT_START_DATE = date(2019, 1, 1)
EM_SOURCE = "em"
EM_REQUEST_PAUSE_RANGE_SECONDS = (0.3, 0.5)
EM_RETRY_DELAYS_SECONDS = (2.0, 5.0)
EM_CONSECUTIVE_FAILURE_LIMIT = 3
EM_CONNECT_TIMEOUT_SECONDS = 5.0
EM_READ_TIMEOUT_SECONDS = 20.0
SQLITE_LOCK_RETRY_DELAYS_SECONDS = (0.5, 1.5, 3.0)
EM_HOST_LOCK_PATH = Path("data/valuation-em-host.lock")
SQL_SYMBOL_CHUNK_SIZE = 500
_EM_VALUE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_EM_REMOTE_COLUMNS = {
    "TRADE_DATE": "trade_date",
    "PE_TTM": "pe_ttm",
    "PB_MRQ": "pb_mrq",
    "PS_TTM": "ps_ttm",
}
_THROTTLE_MARKERS = (
    "403",
    "412",
    "429",
    "captcha",
    "forbidden",
    "rate limit",
    "too many",
    "访问频繁",
    "封禁",
    "限流",
    "风控",
)


class _EastmoneyNetworkError(DataProviderError):
    """A retryable transport failure that must not be mislabeled as throttling."""


class _EastmoneyThrottleError(DataProviderError):
    """An explicit Eastmoney rate-limit or access-control response."""


@dataclass(slots=True)
class _ValuationProgress:
    started: float
    start_date: date
    end_date: date
    symbols_total: int
    symbols_processed: int = 0
    symbols_done: int = 0
    symbols_with_data: int = 0
    symbols_no_data: int = 0
    symbols_skipped_complete: int = 0
    symbols_failed: int = 0
    provider_calls: int = 0
    provider_attempts: int = 0
    rows_fetched: int = 0
    rows_inserted: int = 0
    last_symbol: str | None = None
    resume_symbol: str | None = None
    em_throttled: bool = False
    network_unavailable: bool = False
    pause_reason: str | None = None
    throttle_reason: str | None = None
    is_complete: bool = True
    failures: list[dict[str, str]] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)

    def record_failure(self, symbol: str, exc: Exception) -> None:
        self.symbols_failed += 1
        if self.resume_symbol is None:
            self.resume_symbol = symbol
        if len(self.failures) < 20:
            self.failures.append(
                {
                    "symbol": symbol,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": EM_SOURCE,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "symbols_total": self.symbols_total,
            "symbols_processed": self.symbols_processed,
            "symbols_done": self.symbols_done,
            "symbols_with_data": self.symbols_with_data,
            "symbols_no_data": self.symbols_no_data,
            "symbols_skipped_complete": self.symbols_skipped_complete,
            "symbols_failed": self.symbols_failed,
            "provider_calls": self.provider_calls,
            "provider_attempts": self.provider_attempts,
            "rows_fetched": self.rows_fetched,
            "rows_inserted": self.rows_inserted,
            "last_symbol": self.last_symbol,
            "resume_symbol": self.resume_symbol,
            "em_throttled": self.em_throttled,
            "network_unavailable": self.network_unavailable,
            "pause_reason": self.pause_reason,
            "throttle_reason": self.throttle_reason,
            "is_complete": self.is_complete,
            "request_timeout_seconds": {
                "connect": EM_CONNECT_TIMEOUT_SECONDS,
                "read": EM_READ_TIMEOUT_SECONDS,
            },
            "available_time_basis": "trade_date_15:00_Asia/Shanghai_to_UTC",
            "failures": list(self.failures),
            "coverage": dict(self.coverage),
            "duration_seconds": round(monotonic() - self.started, 2),
        }


def _validate_symbol(symbol: str) -> str:
    normalized = symbol.strip()
    if len(normalized) != 6 or not normalized.isdigit():
        raise ValueError("Eastmoney valuation symbols must be exact six-digit codes")
    return normalized


def fetch_valuation_em(
    symbol: str,
    *,
    start_date: date = DEFAULT_START_DATE,
    end_date: date | None = None,
    http_get: Callable[..., httpx.Response] | None = None,
) -> pd.DataFrame:
    """Fetch and normalize one stock's Eastmoney historical valuation series.

    AKShare's current ``stock_value_em`` helper calls ``requests.get`` without a
    timeout.  Use the same audited Eastmoney endpoint and query contract directly
    so a network outage cannot leave the one-shot backfill blocked indefinitely.
    """

    normalized_symbol = _validate_symbol(symbol)
    if end_date is not None and end_date < start_date:
        raise ValueError("end_date must not be earlier than start_date")

    request_get = http_get or httpx.get
    try:
        response = request_get(
            _EM_VALUE_URL,
            params={
                "sortColumns": "TRADE_DATE",
                "sortTypes": "-1",
                "pageSize": "5000",
                "pageNumber": "1",
                "reportName": "RPT_VALUEANALYSIS_DET",
                "columns": "ALL",
                "quoteColumns": "",
                "source": "WEB",
                "client": "WEB",
                "filter": f'(SECURITY_CODE="{normalized_symbol}")',
            },
            timeout=httpx.Timeout(
                EM_READ_TIMEOUT_SECONDS,
                connect=EM_CONNECT_TIMEOUT_SECONDS,
            ),
        )
    except httpx.TimeoutException as exc:
        raise _EastmoneyNetworkError(
            "Eastmoney stock_value_em request timed out "
            f"(connect={EM_CONNECT_TIMEOUT_SECONDS}s, read={EM_READ_TIMEOUT_SECONDS}s)"
        ) from exc
    except httpx.TransportError as exc:
        raise _EastmoneyNetworkError(
            f"Eastmoney stock_value_em network unavailable: {exc}"
        ) from exc

    if response.status_code in {403, 412, 429}:
        raise _EastmoneyThrottleError(
            f"Eastmoney stock_value_em HTTP {response.status_code}"
        )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise DataProviderError(
            f"Eastmoney stock_value_em HTTP {response.status_code}"
        ) from exc
    try:
        payload: object = response.json()
    except ValueError as exc:
        raise DataProviderError("Eastmoney stock_value_em returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise DataProviderError("Eastmoney stock_value_em returned a non-object payload")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise DataProviderError("Eastmoney stock_value_em payload missing result")
    records = result.get("data")
    if records is None:
        records = []
    if not isinstance(records, list):
        raise DataProviderError("Eastmoney stock_value_em payload data is not a list")
    if not records:
        return pd.DataFrame(columns=tuple(_EM_REMOTE_COLUMNS.values()))

    raw = pd.DataFrame(records)
    missing = set(_EM_REMOTE_COLUMNS).difference(str(column) for column in raw.columns)
    if missing:
        raise DataProviderError(
            f"Eastmoney valuation schema missing columns: {sorted(missing)}"
        )

    frame = (
        raw.loc[:, list(_EM_REMOTE_COLUMNS)]
        .rename(columns=_EM_REMOTE_COLUMNS)
        .copy()
    )
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
    for column in ("pe_ttm", "pb_mrq", "ps_ttm"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["trade_date"])
    frame = frame.loc[frame["trade_date"] >= start_date]
    if end_date is not None:
        frame = frame.loc[frame["trade_date"] <= end_date]
    return (
        frame.drop_duplicates(subset=["trade_date"], keep="last")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )


def _latest_audited_daily_bar() -> date:
    with get_session() as session:
        latest = session.scalar(
            select(func.max(DailyBar.trade_date)).where(
                DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES)
            )
        )
    if not isinstance(latest, date):
        raise DataProviderError("no audited daily bars are available for valuation cutoff")
    return latest


def _universe(symbols: list[str] | None) -> list[str]:
    requested = None if symbols is None else {_validate_symbol(symbol) for symbol in symbols}
    with get_session() as session:
        available = [
            str(symbol)
            for symbol in session.scalars(select(Security.symbol).order_by(Security.symbol)).all()
            if len(str(symbol)) == 6 and str(symbol).isdigit()
        ]
    if requested is None:
        return available
    return [symbol for symbol in available if symbol in requested]


def _latest_dates(symbols: list[str]) -> dict[str, date]:
    if not symbols:
        return {}
    latest_dates: dict[str, date] = {}
    with get_session() as session:
        for offset in range(0, len(symbols), SQL_SYMBOL_CHUNK_SIZE):
            symbol_chunk = symbols[offset : offset + SQL_SYMBOL_CHUNK_SIZE]
            rows = session.execute(
                select(
                    ValuationDaily.symbol,
                    func.max(ValuationDaily.trade_date),
                )
                .where(ValuationDaily.symbol.in_(symbol_chunk))
                .group_by(ValuationDaily.symbol)
            ).all()
            latest_dates.update(
                {
                    str(symbol): latest
                    for symbol, latest in rows
                    if isinstance(latest, date)
                }
            )
    return latest_dates


def _available_time(trade_date: date) -> datetime:
    market_close = datetime.combine(
        trade_date,
        time(hour=15),
        tzinfo=MARKET_TIMEZONE,
    )
    return market_close.astimezone(UTC)


def _finite_or_none(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_sqlite_write_lock(exc: Exception) -> bool:
    return isinstance(exc, OperationalError) and "database is locked" in str(exc).lower()


def _save_rows(symbol: str, frame: pd.DataFrame) -> int:
    values = [
        {
            "symbol": symbol,
            "trade_date": trade_date,
            "pe_ttm": _finite_or_none(row.pe_ttm),
            "pb_mrq": _finite_or_none(row.pb_mrq),
            "ps_ttm": _finite_or_none(row.ps_ttm),
            "source": EM_SOURCE,
            "available_time": _available_time(trade_date),
        }
        for row in frame.itertuples(index=False)
        if isinstance((trade_date := row.trade_date), date)
    ]
    if not values:
        return 0

    retry_count = 0
    while True:
        try:
            with get_session() as session:
                session.add_all(ValuationDaily(**item) for item in values)
            return len(values)
        except OperationalError as exc:
            if (
                not _is_sqlite_write_lock(exc)
                or retry_count >= len(SQLITE_LOCK_RETRY_DELAYS_SECONDS)
            ):
                raise
            delay = SQLITE_LOCK_RETRY_DELAYS_SECONDS[retry_count]
            retry_count += 1
            logger.warning(
                "valuation SQLite lock symbol=%s retry=%s/%s delay=%ss",
                symbol,
                retry_count,
                len(SQLITE_LOCK_RETRY_DELAYS_SECONDS),
                delay,
            )
            sleep(delay)


def _looks_throttled(exc: Exception) -> bool:
    if isinstance(exc, _EastmoneyThrottleError):
        return True
    message = f"{type(exc).__name__}: {exc}".lower()
    return any(marker.lower() in message for marker in _THROTTLE_MARKERS)


def _looks_network_unavailable(exc: Exception) -> bool:
    return isinstance(exc, _EastmoneyNetworkError)


def _fetch_with_retry(
    symbol: str,
    *,
    start_date: date,
    end_date: date,
    progress: _ValuationProgress,
) -> pd.DataFrame:
    last_error: Exception | None = None
    progress.provider_calls += 1
    for attempt in range(len(EM_RETRY_DELAYS_SECONDS) + 1):
        progress.provider_attempts += 1
        try:
            return fetch_valuation_em(
                symbol,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception as exc:
            last_error = exc
            if _looks_throttled(exc) or attempt >= len(EM_RETRY_DELAYS_SECONDS):
                break
            delay = EM_RETRY_DELAYS_SECONDS[attempt]
            logger.warning(
                "Eastmoney valuation retry symbol=%s attempt=%s/%s delay=%ss error=%s",
                symbol,
                attempt + 1,
                len(EM_RETRY_DELAYS_SECONDS) + 1,
                delay,
                exc,
            )
            sleep(delay)
    assert last_error is not None
    raise last_error


@contextmanager
def _em_host_lock() -> Iterator[None]:
    EM_HOST_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle: TextIO = EM_HOST_LOCK_PATH.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DataProviderError(
                f"Eastmoney valuation host lock is held: {EM_HOST_LOCK_PATH}"
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _coverage(
    *,
    start_date: date,
    end_date: date,
    symbols: list[str],
) -> dict[str, Any]:
    total_rows = 0
    covered_symbols = 0
    pe_rows = 0
    pb_rows = 0
    ps_rows = 0
    minimum_date: date | None = None
    maximum_date: date | None = None
    with get_session() as session:
        for offset in range(0, len(symbols), SQL_SYMBOL_CHUNK_SIZE):
            symbol_chunk = symbols[offset : offset + SQL_SYMBOL_CHUNK_SIZE]
            row = session.execute(
                select(
                    func.count(ValuationDaily.id),
                    func.count(func.distinct(ValuationDaily.symbol)),
                    func.min(ValuationDaily.trade_date),
                    func.max(ValuationDaily.trade_date),
                    func.count(ValuationDaily.pe_ttm),
                    func.count(ValuationDaily.pb_mrq),
                    func.count(ValuationDaily.ps_ttm),
                ).where(
                    ValuationDaily.symbol.in_(symbol_chunk),
                    ValuationDaily.trade_date >= start_date,
                    ValuationDaily.trade_date <= end_date,
                )
            ).one()
            total_rows += int(row[0] or 0)
            covered_symbols += int(row[1] or 0)
            if isinstance(row[2], date):
                minimum_date = min(minimum_date, row[2]) if minimum_date else row[2]
            if isinstance(row[3], date):
                maximum_date = max(maximum_date, row[3]) if maximum_date else row[3]
            pe_rows += int(row[4] or 0)
            pb_rows += int(row[5] or 0)
            ps_rows += int(row[6] or 0)
    denominator = total_rows or 1
    return {
        "rows": total_rows,
        "symbols": covered_symbols,
        "symbol_coverage": round(covered_symbols / len(symbols), 6) if symbols else 0.0,
        "min_trade_date": minimum_date.isoformat() if minimum_date else None,
        "max_trade_date": maximum_date.isoformat() if maximum_date else None,
        "pe_ttm_non_null_rate": round(pe_rows / denominator, 6),
        "pb_mrq_non_null_rate": round(pb_rows / denominator, 6),
        "ps_ttm_non_null_rate": round(ps_rows / denominator, 6),
    }


def backfill_valuation(
    *,
    start_date: date = DEFAULT_START_DATE,
    end_date: date | None = None,
    symbols: list[str] | None = None,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Backfill PIT valuation history from Eastmoney with resumable checkpoints."""

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    resolved_end = end_date or _latest_audited_daily_bar()
    if resolved_end < start_date:
        raise ValueError("end_date must not be earlier than start_date")
    universe = _universe(symbols)
    progress = _ValuationProgress(
        started=monotonic(),
        start_date=start_date,
        end_date=resolved_end,
        symbols_total=len(universe),
    )

    try:
        lock_context = _em_host_lock()
        with lock_context:
            latest_dates = _latest_dates(universe)
            consecutive_failures = 0
            for processed, symbol in enumerate(universe, start=1):
                progress.symbols_processed = processed
                progress.last_symbol = symbol
                latest = latest_dates.get(symbol)
                fetch_start = max(
                    start_date,
                    latest + timedelta(days=1) if latest is not None else start_date,
                )
                if fetch_start > resolved_end:
                    progress.symbols_done += 1
                    progress.symbols_skipped_complete += 1
                    consecutive_failures = 0
                    continue

                try:
                    frame = _fetch_with_retry(
                        symbol,
                        start_date=fetch_start,
                        end_date=resolved_end,
                        progress=progress,
                    )
                    progress.rows_fetched += len(frame)
                    if frame.empty:
                        progress.symbols_no_data += 1
                    else:
                        progress.rows_inserted += _save_rows(symbol, frame)
                        progress.symbols_with_data += 1
                    progress.symbols_done += 1
                    consecutive_failures = 0
                except Exception as exc:
                    progress.record_failure(symbol, exc)
                    consecutive_failures += 1
                    if _looks_throttled(exc):
                        progress.em_throttled = True
                        progress.throttle_reason = f"{type(exc).__name__}: {exc}"[:500]
                        progress.pause_reason = "em_throttled"
                        progress.is_complete = False
                        raise JobExecutionError(
                            "Eastmoney valuation backfill paused after provider throttling "
                            f"signals; processed={processed}, resume_symbol={symbol}",
                            stats=progress.as_dict(),
                        ) from exc
                    if _looks_network_unavailable(exc):
                        progress.network_unavailable = True
                        progress.pause_reason = "network_unavailable"
                        progress.is_complete = False
                        raise JobExecutionError(
                            "Eastmoney valuation backfill paused after network outage; "
                            f"processed={processed}, resume_symbol={symbol}",
                            stats=progress.as_dict(),
                        ) from exc
                    if consecutive_failures >= EM_CONSECUTIVE_FAILURE_LIMIT:
                        progress.pause_reason = "provider_unavailable"
                        progress.is_complete = False
                        raise JobExecutionError(
                            "Eastmoney valuation backfill paused after consecutive "
                            f"provider failures; processed={processed}, "
                            f"resume_symbol={symbol}",
                            stats=progress.as_dict(),
                        ) from exc
                finally:
                    pause_seconds = uniform(*EM_REQUEST_PAUSE_RANGE_SECONDS)
                    sleep(pause_seconds)

                if processed % batch_size == 0:
                    logger.info(
                        "valuation backfill progress processed=%s total=%s inserted=%s "
                        "failed=%s",
                        processed,
                        len(universe),
                        progress.rows_inserted,
                        progress.symbols_failed,
                    )
    except DataProviderError as exc:
        progress.is_complete = False
        progress.resume_symbol = progress.last_symbol or (universe[0] if universe else None)
        raise JobExecutionError(str(exc), stats=progress.as_dict()) from exc

    progress.coverage = _coverage(
        start_date=start_date,
        end_date=resolved_end,
        symbols=universe,
    )
    if progress.symbols_failed:
        progress.is_complete = False
        raise JobExecutionError(
            f"Eastmoney valuation backfill completed with {progress.symbols_failed} "
            "unresolved symbol failures",
            stats=progress.as_dict(),
        )
    return progress.as_dict()


def sync_valuation_daily(lookback_days: int = 10) -> dict[str, Any]:
    """Increment valuation history after daily bars have completed."""

    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    end_date = _latest_audited_daily_bar()
    return backfill_valuation(
        start_date=end_date - timedelta(days=lookback_days),
        end_date=end_date,
    )


def register_valuation_jobs() -> None:
    register(
        JobSpec(
            name="backfill_valuation",
            func=backfill_valuation,
            trigger=None,
        )
    )
    register(
        JobSpec(
            name="sync_valuation_daily",
            func=sync_valuation_daily,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=18,
                minute=50,
                timezone=MARKET_TIMEZONE,
            ),
            enabled_key="valuation_sync_enabled",
        )
    )
