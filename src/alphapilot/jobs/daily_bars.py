from __future__ import annotations

import logging
from datetime import date, timedelta
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select

from alphapilot.data.baostock_provider import BaoStockMarketDataProvider
from alphapilot.data.base import DataProviderError, MarketDataProvider
from alphapilot.data.sina_provider import SinaDailyBarProvider
from alphapilot.db.engine import get_session
from alphapilot.db.models import DailyBar, Security
from alphapilot.jobs.registry import JobSpec, register
from alphapilot.services.market_data import latest_trade_date, save_bars

logger = logging.getLogger(__name__)


def _is_bse(symbol: str, board: str | None) -> bool:
    return board == "北交所" or symbol.startswith(("4", "8", "92"))


def _provider_for(
    symbol: str,
    board: str | None,
    baostock: MarketDataProvider,
    sina: MarketDataProvider,
) -> MarketDataProvider:
    return sina if _is_bse(symbol, board) else baostock


def _latest_provider_trade_date(
    provider: MarketDataProvider, benchmark_symbol: str, requested_end: date
) -> date:
    """Probe each source so an upstream EOD lag is not treated as mass failure."""

    frame = provider.get_daily_bars(
        benchmark_symbol, requested_end - timedelta(days=10), requested_end
    )
    available = [
        pd.Timestamp(value).date()
        for value in frame["date"]
        if not pd.isna(value)
    ]
    if not available:
        raise DataProviderError(
            f"{provider.name} benchmark probe returned no valid dates"
        )
    return min(max(available), requested_end)


def sync_daily_bars(lookback_days: int = 450, batch_size: int = 200) -> dict[str, Any]:
    """Incrementally sync every listed A-share with bounded, committed batches."""

    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    started = monotonic()
    baostock = BaoStockMarketDataProvider()
    sina = SinaDailyBarProvider()
    done = 0
    skipped = 0
    failed_count = 0
    consecutive_failures = 0
    rows_inserted = 0
    failures: list[dict[str, str]] = []
    source_counts: dict[str, int] = {}

    with get_session() as session:
        securities = session.execute(
            select(Security.symbol, Security.board).order_by(Security.symbol)
        ).all()
        latest_by_symbol = {
            str(symbol): trade_date
            for symbol, trade_date in session.execute(
                select(DailyBar.symbol, func.max(DailyBar.trade_date)).group_by(
                    DailyBar.symbol
                )
            ).all()
            if isinstance(trade_date, date)
        }
        end = latest_trade_date(session)
        provider_trade_dates = {
            baostock.name: _latest_provider_trade_date(baostock, "SH.000001", end),
            sina.name: _latest_provider_trade_date(sina, "920000", end),
        }

        for processed, (symbol, board) in enumerate(securities, start=1):
            provider = _provider_for(symbol, board, baostock, sina)
            provider_end = provider_trade_dates[provider.name]
            last_date = latest_by_symbol.get(symbol)
            start = (
                last_date + timedelta(days=1)
                if last_date is not None
                else provider_end - timedelta(days=lookback_days)
            )
            if start > provider_end:
                skipped += 1
                consecutive_failures = 0
            else:
                try:
                    frame = provider.get_daily_bars(symbol, start, provider_end)
                    with session.begin_nested():
                        inserted = save_bars(session, symbol, frame, provider.name)
                    rows_inserted += inserted
                    done += 1
                    source_counts[provider.name] = source_counts.get(provider.name, 0) + 1
                    consecutive_failures = 0
                except Exception as exc:
                    failed_count += 1
                    consecutive_failures += 1
                    if len(failures) < 20:
                        failures.append(
                            {
                                "symbol": symbol,
                                "error": f"{type(exc).__name__}: {exc}"[:500],
                            }
                        )
                    if consecutive_failures >= 20:
                        session.commit()
                        raise DataProviderError(
                            "daily bar sync stopped after 20 consecutive failures; "
                            f"processed={processed}, rows_inserted={rows_inserted}, "
                            f"last_symbol={symbol}"
                        ) from exc

            if processed % batch_size == 0:
                session.commit()
                session.expunge_all()
                logger.info(
                    "daily bar sync progress processed=%s total=%s rows=%s failed=%s",
                    processed,
                    len(securities),
                    rows_inserted,
                    failed_count,
                )

        session.commit()

    return {
        "total": len(securities),
        "done": done,
        "skipped": skipped,
        "failed": failures,
        "failed_count": failed_count,
        "rows_inserted": rows_inserted,
        "latest_trade_date": end.isoformat(),
        "provider_trade_dates": {
            source: trade_date.isoformat()
            for source, trade_date in provider_trade_dates.items()
        },
        "source_counts": source_counts,
        "duration_seconds": round(monotonic() - started, 2),
    }


def register_daily_bars_job() -> None:
    register(
        JobSpec(
            name="sync_daily_bars",
            func=sync_daily_bars,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=17,
                minute=30,
                timezone=ZoneInfo("Asia/Shanghai"),
            ),
        )
    )
