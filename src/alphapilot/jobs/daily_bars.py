from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from time import monotonic, sleep
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from alphapilot.data.baostock_provider import BaoStockMarketDataProvider
from alphapilot.data.base import (
    DataProviderError,
    EmptyDailyBarsError,
    MarketDataProvider,
)
from alphapilot.data.provenance import AUDITED_DAILY_BAR_SOURCES
from alphapilot.data.sina_provider import SinaDailyBarProvider
from alphapilot.db.engine import get_session
from alphapilot.db.models import DailyBar, Security
from alphapilot.jobs.registry import JobExecutionError, JobSpec, register
from alphapilot.services.market_data import latest_trade_date, save_bars

logger = logging.getLogger(__name__)

_SQLITE_LOCK_RETRY_DELAYS = (0.5, 1.5, 3.0)


@dataclass(slots=True)
class _SyncProgress:
    started: float
    total: int
    latest_trade_date: date
    provider_trade_dates: dict[str, date]
    provider_probe_starts: dict[str, date]
    processed: int = 0
    done: int = 0
    skipped: int = 0
    not_published: int = 0
    failed_count: int = 0
    rows_inserted: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)
    source_counts: dict[str, int] = field(default_factory=dict)

    def record_failure(self, symbol: str, exc: Exception) -> None:
        self.failed_count += 1
        if len(self.failures) < 20:
            self.failures.append(
                {
                    "symbol": symbol,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "processed": self.processed,
            "done": self.done,
            "skipped": self.skipped,
            "not_published": self.not_published,
            "failed": list(self.failures),
            "failed_count": self.failed_count,
            "rows_inserted": self.rows_inserted,
            "latest_trade_date": self.latest_trade_date.isoformat(),
            "provider_trade_dates": {
                source: trade_date.isoformat()
                for source, trade_date in self.provider_trade_dates.items()
            },
            "provider_probe_starts": {
                source: probe_start.isoformat()
                for source, probe_start in self.provider_probe_starts.items()
            },
            "source_counts": dict(self.source_counts),
            "duration_seconds": round(monotonic() - self.started, 2),
        }


@dataclass(frozen=True, slots=True)
class _ProviderTradeWindow:
    probed_from: date
    latest: date
    available_dates: frozenset[date]

    def contains_only_latest(self, start: date) -> bool:
        """Return true only when the probed calendar proves one expected trade day."""

        if start < self.probed_from:
            return False
        expected = {
            trade_date
            for trade_date in self.available_dates
            if start <= trade_date <= self.latest
        }
        return expected == {self.latest}


def _is_bse(symbol: str, board: str | None) -> bool:
    return board == "北交所" or symbol.startswith(("4", "8", "92"))


def _provider_for(
    symbol: str,
    board: str | None,
    baostock: MarketDataProvider,
    sina: MarketDataProvider,
) -> MarketDataProvider:
    return sina if _is_bse(symbol, board) else baostock


def _is_sqlite_write_lock(exc: Exception) -> bool:
    return isinstance(exc, OperationalError) and "database is locked" in str(exc).lower()


def _save_bars_with_lock_retry(
    symbol: str,
    frame: pd.DataFrame,
    source: str,
) -> int:
    """Retry SQLite writes in fresh transactions without refetching market data."""

    retry_count = 0
    while True:
        try:
            # A WAL read transaction can fail its write upgrade with
            # SQLITE_BUSY_SNAPSHOT. Retrying a savepoint in that same Session
            # cannot refresh the snapshot, so every attempt gets a short,
            # independently committed write transaction.
            with get_session() as write_session:
                return save_bars(write_session, symbol, frame, source)
        except OperationalError as exc:
            if not _is_sqlite_write_lock(exc) or retry_count >= len(
                _SQLITE_LOCK_RETRY_DELAYS
            ):
                raise
            delay = _SQLITE_LOCK_RETRY_DELAYS[retry_count]
            retry_count += 1
            logger.warning(
                "daily bars SQLite lock symbol=%s retry=%s/%s delay=%ss",
                symbol,
                retry_count,
                len(_SQLITE_LOCK_RETRY_DELAYS),
                delay,
            )
            sleep(delay)


def _probe_provider_trade_window(
    provider: MarketDataProvider, benchmark_symbol: str, requested_end: date
) -> _ProviderTradeWindow:
    """Probe each source so an upstream EOD lag is not treated as mass failure."""

    probed_from = requested_end - timedelta(days=10)
    frame = provider.get_daily_bars(
        benchmark_symbol, probed_from, requested_end
    )
    available = {
        pd.Timestamp(value).date()
        for value in frame["date"]
        if not pd.isna(value)
    }
    if not available:
        raise DataProviderError(
            f"{provider.name} benchmark probe returned no valid dates"
        )
    latest = min(max(available), requested_end)
    return _ProviderTradeWindow(
        probed_from=probed_from,
        latest=latest,
        available_dates=frozenset(
            trade_date for trade_date in available if trade_date <= latest
        ),
    )


def sync_daily_bars(lookback_days: int = 450, batch_size: int = 200) -> dict[str, Any]:
    """Incrementally sync every listed A-share with short committed writes."""

    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    started = monotonic()
    baostock = BaoStockMarketDataProvider()
    sina = SinaDailyBarProvider()
    consecutive_failures = 0

    # Take one short metadata snapshot, then release it before any provider I/O
    # or writes. This prevents a long-lived WAL read snapshot from poisoning a
    # later write upgrade after another scheduler job commits.
    with get_session() as session:
        securities = session.execute(
            select(Security.symbol, Security.board).order_by(Security.symbol)
        ).all()
        latest_by_symbol = {
            str(symbol): trade_date
            for symbol, trade_date in session.execute(
                select(DailyBar.symbol, func.max(DailyBar.trade_date))
                .where(DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES))
                .group_by(DailyBar.symbol)
            ).all()
            if isinstance(trade_date, date)
        }
        end = latest_trade_date(session)

    provider_windows = {
        baostock.name: _probe_provider_trade_window(baostock, "SH.000001", end),
        sina.name: _probe_provider_trade_window(sina, "920000", end),
    }
    provider_trade_dates = {
        source: window.latest for source, window in provider_windows.items()
    }
    progress = _SyncProgress(
        started=started,
        total=len(securities),
        latest_trade_date=end,
        provider_trade_dates=provider_trade_dates,
        provider_probe_starts={
            source: window.probed_from for source, window in provider_windows.items()
        },
    )

    for processed, (symbol, board) in enumerate(securities, start=1):
        progress.processed = processed
        provider = _provider_for(symbol, board, baostock, sina)
        provider_window = provider_windows[provider.name]
        provider_end = provider_window.latest
        last_date = latest_by_symbol.get(symbol)
        start = (
            last_date + timedelta(days=1)
            if last_date is not None
            else provider_end - timedelta(days=lookback_days)
        )
        if start > provider_end:
            progress.skipped += 1
            consecutive_failures = 0
        else:
            failure: Exception | None = None
            sqlite_lock_failure = False
            try:
                frame = provider.get_daily_bars(symbol, start, provider_end)
                if frame.empty:
                    raise EmptyDailyBarsError(
                        f"{provider.name} returned no daily bars for {symbol}"
                    )
                inserted = _save_bars_with_lock_retry(symbol, frame, provider.name)
                progress.rows_inserted += inserted
                progress.done += 1
                progress.source_counts[provider.name] = (
                    progress.source_counts.get(provider.name, 0) + 1
                )
                consecutive_failures = 0
            except EmptyDailyBarsError as exc:
                if last_date is not None and provider_window.contains_only_latest(start):
                    progress.not_published += 1
                    consecutive_failures = 0
                else:
                    failure = exc
            except Exception as exc:
                failure = exc
                sqlite_lock_failure = _is_sqlite_write_lock(exc)

            if failure is not None:
                progress.record_failure(symbol, failure)
                if sqlite_lock_failure:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= 20:
                        raise JobExecutionError(
                            "daily bar sync stopped after 20 consecutive failures; "
                            f"processed={processed}, "
                            f"rows_inserted={progress.rows_inserted}, "
                            f"last_symbol={symbol}",
                            stats=progress.as_dict(),
                        ) from failure

        if processed % batch_size == 0:
            logger.info(
                "daily bar sync progress processed=%s total=%s rows=%s failed=%s",
                processed,
                len(securities),
                progress.rows_inserted,
                progress.failed_count,
            )

    return progress.as_dict()


def register_daily_bars_job() -> None:
    register(
        JobSpec(
            name="sync_daily_bars",
            func=sync_daily_bars,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=18,
                minute=40,
                timezone=ZoneInfo("Asia/Shanghai"),
            ),
        )
    )
