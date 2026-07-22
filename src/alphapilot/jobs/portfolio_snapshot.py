from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from apscheduler.triggers.cron import CronTrigger

from alphapilot.core.config import get_settings
from alphapilot.data.base import DataProviderError
from alphapilot.data.futu_provider import FutuMarketDataProvider
from alphapilot.db.engine import get_session
from alphapilot.db.models import PortfolioSnapshot
from alphapilot.futu.client import FutuClient, FutuClientError, get_futu_client
from alphapilot.jobs.registry import JobExecutionError, JobSpec, register
from alphapilot.services.broker import (
    BrokerError,
    fetch_account_funds,
    fetch_positions,
)
from alphapilot.services.executor import paper_execution_guard
from alphapilot.services.portfolio import (
    BENCHMARK_SYMBOL,
    PortfolioServiceError,
    recompute_portfolio_metrics,
    upsert_benchmark_close_bar,
    upsert_portfolio_snapshot,
)

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
SNAPSHOT_READY_TIME = time(15, 10)
BENCHMARK_LOOKBACK_DAYS = 10


def _market_now() -> datetime:
    return datetime.now(MARKET_TIMEZONE)


def _base_stats(target: date) -> dict[str, Any]:
    return {
        "trade_date": target.isoformat(),
        "skipped": None,
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "benchmark_bar": None,
        "inserted": 0,
        "updated": 0,
        "positions": 0,
        "missing_industry_count": 0,
        "warning_count": 0,
        "warnings": [],
    }


def _warning(stats: dict[str, Any], message: str) -> None:
    warnings = stats["warnings"]
    assert isinstance(warnings, list)
    warnings.append(message)
    stats["warning_count"] = len(warnings)


def _cn_trade_day(client: FutuClient, target: date) -> bool:
    value = target.isoformat()
    calendar = client.quote_call_raw(
        "request_trading_days",
        args=["CN", value, value],
    )
    if not isinstance(calendar, list):
        raise DataProviderError("富途 A 股交易日历返回格式异常。")
    return any(
        isinstance(raw, Mapping) and str(raw.get("time") or "").strip() == value for raw in calendar
    )


def _benchmark_record(client: FutuClient, target: date) -> dict[str, Any]:
    provider = FutuMarketDataProvider(get_settings(), client=client)
    try:
        frame = provider.get_daily_bars(
            BENCHMARK_SYMBOL,
            target - timedelta(days=BENCHMARK_LOOKBACK_DAYS),
            target,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DataProviderError("沪深300收盘日线返回格式异常。") from exc
    if frame.empty:
        raise DataProviderError("沪深300收盘日线为空。")
    parsed_dates = pd.to_datetime(frame["date"], errors="coerce").dt.date
    selected = frame.loc[parsed_dates == target]
    if len(selected.index) != 1:
        raise DataProviderError("沪深300未返回唯一的当日收盘日线。")
    return {str(key): value for key, value in selected.iloc[0].to_dict().items()}


def _before_close(now: datetime) -> bool:
    return now.time().replace(tzinfo=None) < SNAPSHOT_READY_TIME


def _job_failure(
    message: str,
    stats: dict[str, Any],
    exc: Exception,
) -> JobExecutionError:
    stats["failure_type"] = type(exc).__name__
    return JobExecutionError(message, stats=stats)


def snapshot_portfolio(
    client: FutuClient | None = None,
) -> dict[str, Any]:
    """Persist one post-close SIMULATE snapshot without holding DB over the network."""

    started = monotonic()
    now = _market_now()
    target = now.date()
    stats = _base_stats(target)
    if _before_close(now):
        stats["skipped"] = "before_close"
        stats["duration_seconds"] = round(monotonic() - started, 2)
        return stats

    resolved_client = client or get_futu_client()
    try:
        if not _cn_trade_day(resolved_client, target):
            stats["skipped"] = "non_trading_day"
            stats["duration_seconds"] = round(monotonic() - started, 2)
            return stats
    except (DataProviderError, FutuClientError) as exc:
        raise _job_failure("无法确认 A 股交易日，组合快照未写入。", stats, exc) from exc

    benchmark: dict[str, Any] | None = None
    try:
        benchmark = _benchmark_record(resolved_client, target)
    except (DataProviderError, FutuClientError) as exc:
        _warning(
            stats,
            f"沪深300当日完整收盘暂不可用（{type(exc).__name__}），"
            "benchmark 保留为 null，等待收盘后对账任务。",
        )

    try:
        with paper_execution_guard():
            funds = fetch_account_funds(resolved_client)
            positions = fetch_positions(resolved_client)
    except (BrokerError, FutuClientError) as exc:
        raise _job_failure("富途模拟账户暂不可用，组合快照未写入。", stats, exc) from exc

    try:
        with get_session() as session:
            if benchmark is not None:
                stats["benchmark_bar"] = upsert_benchmark_close_bar(
                    session,
                    target,
                    benchmark,
                    source="futu-close",
                )
            _, persisted = upsert_portfolio_snapshot(
                session,
                target,
                funds,
                positions,
            )
            stats.update(persisted)
    except PortfolioServiceError as exc:
        raise _job_failure("组合快照数据校验失败，本次未写入。", stats, exc) from exc
    stats["duration_seconds"] = round(monotonic() - started, 2)
    return stats


def reconcile_portfolio_benchmark(
    client: FutuClient | None = None,
    trade_date: date | None = None,
) -> dict[str, Any]:
    """Correct the close benchmark after upstream daily-bar tasks and recompute metrics."""

    started = monotonic()
    now = _market_now()
    target = trade_date or now.date()
    stats = _base_stats(target)
    if target > now.date():
        stats["skipped"] = "future_date"
        stats["duration_seconds"] = round(monotonic() - started, 2)
        return stats
    if target == now.date() and _before_close(now):
        stats["skipped"] = "before_close"
        stats["duration_seconds"] = round(monotonic() - started, 2)
        return stats
    resolved_client = client or get_futu_client()
    try:
        if not _cn_trade_day(resolved_client, target):
            stats["skipped"] = "non_trading_day"
            stats["duration_seconds"] = round(monotonic() - started, 2)
            return stats
        benchmark = _benchmark_record(resolved_client, target)
    except (DataProviderError, FutuClientError) as exc:
        raise _job_failure("沪深300收盘日线对账失败，未改动历史归因。", stats, exc) from exc

    try:
        with get_session() as session:
            stats["benchmark_bar"] = upsert_benchmark_close_bar(
                session,
                target,
                benchmark,
                source="futu-close",
            )
            metrics = recompute_portfolio_metrics(session)
            stats.update(metrics)
            stats["snapshot_exists"] = session.get(PortfolioSnapshot, target) is not None
    except PortfolioServiceError as exc:
        raise _job_failure("沪深300收盘日线校验失败，未改动历史归因。", stats, exc) from exc
    stats["duration_seconds"] = round(monotonic() - started, 2)
    return stats


def register_portfolio_jobs() -> None:
    register(
        JobSpec(
            name="snapshot_portfolio",
            func=snapshot_portfolio,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=15,
                minute=10,
                timezone=MARKET_TIMEZONE,
            ),
            enabled_key="paper_trading_enabled",
        )
    )
    register(
        JobSpec(
            name="reconcile_portfolio_benchmark",
            func=reconcile_portfolio_benchmark,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=19,
                minute=0,
                timezone=MARKET_TIMEZONE,
            ),
            enabled_key="paper_trading_enabled",
        )
    )
