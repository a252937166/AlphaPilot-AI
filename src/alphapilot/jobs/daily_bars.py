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
from sqlalchemy.orm import Session

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
_BACKFILL_PROFILE_KEY = "daily_bars_backfill_start"


@dataclass(slots=True)
class _SyncProgress:
    started: float
    total: int
    latest_trade_date: date
    provider_trade_dates: dict[str, date]
    provider_probe_starts: dict[str, date]
    requested_start_date: date | None = None
    processed: int = 0
    done: int = 0
    skipped: int = 0
    not_published: int = 0
    historical_backfill_symbols: int = 0
    backfill_checkpoints_written: int = 0
    no_prior_history: int = 0
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
            "requested_start_date": (
                self.requested_start_date.isoformat()
                if self.requested_start_date is not None
                else None
            ),
            "historical_backfill_symbols": self.historical_backfill_symbols,
            "backfill_checkpoints_written": self.backfill_checkpoints_written,
            "no_prior_history": self.no_prior_history,
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


def _backfill_checkpoint(profile: Any) -> date | None:
    if not isinstance(profile, dict):
        return None
    raw = profile.get(_BACKFILL_PROFILE_KEY)
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _record_backfill_checkpoint(
    session: Session,
    symbol: str,
    start_date: date,
) -> bool:
    security = session.get(Security, symbol)
    if security is None:
        return False
    existing = _backfill_checkpoint(security.profile)
    if existing is not None and existing <= start_date:
        return False
    profile = dict(security.profile) if isinstance(security.profile, dict) else {}
    profile[_BACKFILL_PROFILE_KEY] = start_date.isoformat()
    security.profile = profile
    return True


def _save_bars_with_lock_retry(
    symbol: str,
    frame: pd.DataFrame,
    source: str,
    *,
    backfill_start_date: date | None = None,
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
                inserted = save_bars(write_session, symbol, frame, source)
                if backfill_start_date is not None:
                    _record_backfill_checkpoint(
                        write_session,
                        symbol,
                        backfill_start_date,
                    )
                return inserted
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


def _mark_backfill_with_lock_retry(symbol: str, start_date: date) -> bool:
    retry_count = 0
    while True:
        try:
            with get_session() as write_session:
                return _record_backfill_checkpoint(
                    write_session,
                    symbol,
                    start_date,
                )
        except OperationalError as exc:
            if not _is_sqlite_write_lock(exc) or retry_count >= len(
                _SQLITE_LOCK_RETRY_DELAYS
            ):
                raise
            delay = _SQLITE_LOCK_RETRY_DELAYS[retry_count]
            retry_count += 1
            logger.warning(
                "daily bars checkpoint SQLite lock symbol=%s retry=%s/%s delay=%ss",
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


def sync_daily_bars(
    lookback_days: int = 450,
    batch_size: int = 200,
    *,
    start_date: date | None = None,
) -> dict[str, Any]:
    """Sync every listed A-share, optionally backfilling to an explicit date."""

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
            select(
                Security.symbol,
                Security.board,
                Security.profile,
            ).order_by(Security.symbol)
        ).all()
        bounds_by_symbol = {
            str(symbol): (first_date, last_date)
            for symbol, first_date, last_date in session.execute(
                select(
                    DailyBar.symbol,
                    func.min(DailyBar.trade_date),
                    func.max(DailyBar.trade_date),
                )
                .where(DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES))
                .group_by(DailyBar.symbol)
            ).all()
            if isinstance(first_date, date) and isinstance(last_date, date)
        }
        end = latest_trade_date(session)

    if start_date is not None and start_date > end:
        raise ValueError("start_date must not be after the latest trade date")

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
        requested_start_date=start_date,
    )

    for processed, (symbol, board, profile) in enumerate(securities, start=1):
        progress.processed = processed
        provider = _provider_for(symbol, board, baostock, sina)
        provider_window = provider_windows[provider.name]
        provider_end = provider_window.latest
        bounds = bounds_by_symbol.get(symbol)
        first_date = bounds[0] if bounds is not None else None
        last_date = bounds[1] if bounds is not None else None
        checkpoint = _backfill_checkpoint(profile)
        backfill_is_complete = (
            start_date is not None
            and checkpoint is not None
            and checkpoint <= start_date
        )
        request_windows: list[tuple[date, date, bool]] = []
        if first_date is None or last_date is None:
            request_windows.append(
                (
                    start_date or provider_end - timedelta(days=lookback_days),
                    provider_end,
                    start_date is not None,
                )
            )
        else:
            if (
                start_date is not None
                and not backfill_is_complete
                and first_date > start_date
            ):
                request_windows.append(
                    (
                        start_date,
                        min(provider_end, first_date - timedelta(days=1)),
                        True,
                    )
                )
            if last_date < provider_end:
                request_windows.append(
                    (last_date + timedelta(days=1), provider_end, False)
                )

        request_windows = [
            (window_start, window_end, historical)
            for window_start, window_end, historical in request_windows
            if window_start <= window_end
        ]
        if not request_windows:
            if (
                start_date is not None
                and not backfill_is_complete
                and first_date is not None
                and first_date <= start_date
                and _mark_backfill_with_lock_retry(symbol, start_date)
            ):
                progress.backfill_checkpoints_written += 1
            progress.skipped += 1
            consecutive_failures = 0
        else:
            failure: Exception | None = None
            sqlite_lock_failure = False
            wrote_symbol = False
            historical_requested = False
            for window_start, window_end, historical in request_windows:
                historical_requested = historical_requested or historical
                try:
                    frame = provider.get_daily_bars(
                        symbol,
                        window_start,
                        window_end,
                    )
                    if frame.empty:
                        raise EmptyDailyBarsError(
                            f"{provider.name} returned no daily bars for {symbol}"
                        )
                    inserted = _save_bars_with_lock_retry(
                        symbol,
                        frame,
                        provider.name,
                        backfill_start_date=start_date if historical else None,
                    )
                    progress.rows_inserted += inserted
                    if historical:
                        progress.backfill_checkpoints_written += 1
                    wrote_symbol = True
                except EmptyDailyBarsError as exc:
                    if historical and first_date is not None:
                        # An existing first bar after the requested boundary can be
                        # the listing date. Absence before it is an honest coverage
                        # gap, not an upstream outage or circuit-breaker signal.
                        progress.no_prior_history += 1
                        if (
                            start_date is not None
                            and _mark_backfill_with_lock_retry(symbol, start_date)
                        ):
                            progress.backfill_checkpoints_written += 1
                        continue
                    if (
                        not historical
                        and window_end == provider_end
                        and provider_window.contains_only_latest(window_start)
                    ):
                        progress.not_published += 1
                        continue
                    failure = exc
                    break
                except Exception as exc:
                    failure = exc
                    sqlite_lock_failure = _is_sqlite_write_lock(exc)
                    break

            if historical_requested:
                progress.historical_backfill_symbols += 1
            if failure is None:
                if wrote_symbol:
                    progress.done += 1
                    progress.source_counts[provider.name] = (
                        progress.source_counts.get(provider.name, 0) + 1
                    )
                consecutive_failures = 0
            else:
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
