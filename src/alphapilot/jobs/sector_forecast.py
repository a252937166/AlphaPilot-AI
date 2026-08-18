from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from alphapilot.core.timeutil import iso_utc
from alphapilot.data.provenance import (
    AUDITED_DAILY_BAR_SOURCES,
    AUDITED_SECTOR_FLOW_SOURCES,
)
from alphapilot.db.engine import get_session
from alphapilot.db.market_audit import audited_daily_coverage
from alphapilot.db.models import (
    DailyBar,
    JobRun,
    SectorConstituent,
    SectorFlowDaily,
    SectorForecast,
)
from alphapilot.engines.sector_forecast import BAR_SESSIONS, compute_sector_forecasts
from alphapilot.jobs.registry import JobExecutionError, JobSpec, register

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
UPSTREAM_JOBS = (
    "sync_daily_bars",
    "sync_sector_flows",
    "repair_recent_sector_flow_gaps",
    "backfill_sector_flows",
)
UPSTREAM_LEASES = {
    "sync_daily_bars": timedelta(hours=2),
    "sync_sector_flows": timedelta(minutes=45),
    "repair_recent_sector_flow_gaps": timedelta(minutes=30),
    "backfill_sector_flows": timedelta(hours=2),
}


def _market_today() -> date:
    return datetime.now(MARKET_TIMEZONE).date()


def _as_utc(value: datetime) -> datetime:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).astimezone(UTC)


def _job_now() -> datetime:
    return datetime.now(UTC)


def _upstream_state(
    session: Session,
    *,
    now: datetime,
) -> tuple[list[str], list[dict[str, Any]]]:
    rows = session.scalars(
        select(JobRun)
        .where(
            JobRun.job_name.in_(UPSTREAM_JOBS),
            JobRun.status == "running",
        )
        .order_by(JobRun.started_at, JobRun.id)
    ).all()
    active: set[str] = set()
    stale: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row.started_at, datetime):
            stale.append({"id": row.id, "job_name": row.job_name, "started_at": None})
            continue
        started_at = _as_utc(row.started_at)
        lease = UPSTREAM_LEASES[row.job_name]
        age = max(0.0, (now - started_at).total_seconds())
        if now - started_at <= lease:
            active.add(row.job_name)
            continue
        stale.append(
            {
                "id": row.id,
                "job_name": row.job_name,
                "started_at": iso_utc(row.started_at),
                "age_seconds": round(age, 1),
            }
        )
    return sorted(active), stale


def _latest_benchmark_date(session: Session) -> date | None:
    value = session.scalar(
        select(func.max(DailyBar.trade_date)).where(
            DailyBar.symbol == "SH.000001",
            DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
            DailyBar.close > 0,
        )
    )
    return value if isinstance(value, date) else None


def _input_coverage(session: Session, target_date: date) -> dict[str, Any]:
    coverage = audited_daily_coverage(session, target_date)
    return {
        "trade_date": target_date.isoformat(),
        "reference_trade_date": (
            coverage.reference_trade_date.isoformat()
            if coverage.reference_trade_date is not None
            else None
        ),
        "symbol_count": coverage.symbol_count,
        "reference_symbol_count": coverage.reference_symbol_count,
        "ratio": round(coverage.ratio, 6),
        "minimum_ratio": coverage.minimum_ratio,
        "complete": coverage.complete,
    }


def _input_fingerprint(session: Session, target_date: date) -> str:
    dates = list(
        session.scalars(
            select(DailyBar.trade_date)
            .where(
                DailyBar.symbol == "SH.000001",
                DailyBar.trade_date <= target_date,
                DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
                DailyBar.close > 0,
            )
            .distinct()
            .order_by(DailyBar.trade_date.desc())
            .limit(BAR_SESSIONS)
        ).all()
    )
    if not dates:
        return hashlib.sha256(b"empty-sector-forecast-input").hexdigest()
    bar_values = session.execute(
        select(
            func.count(DailyBar.id),
            func.max(DailyBar.ingested_at),
            func.sum(DailyBar.close),
            func.sum(DailyBar.amount),
        ).where(
            DailyBar.trade_date.in_(dates),
            func.length(DailyBar.symbol) == 6,
            DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
            DailyBar.close > 0,
        )
    ).one()
    member_values = session.execute(
        select(
            func.count(SectorConstituent.id),
            func.max(SectorConstituent.refreshed_at),
        )
    ).one()
    flow_values = session.execute(
        select(
            func.count(SectorFlowDaily.id),
            func.max(SectorFlowDaily.trade_date),
            func.sum(SectorFlowDaily.net_inflow),
            func.sum(SectorFlowDaily.main_inflow),
        ).where(
            SectorFlowDaily.trade_date.in_(dates),
            SectorFlowDaily.source.in_(AUDITED_SECTOR_FLOW_SOURCES),
        )
    ).one()
    payload = repr(
        (
            min(dates),
            max(dates),
            tuple(bar_values),
            tuple(member_values),
            tuple(flow_values),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _empty_stats(
    *,
    target_date: date | None,
    skipped: str,
    duration_seconds: float,
) -> dict[str, Any]:
    return {
        "date": target_date.isoformat() if target_date is not None else None,
        "plates": 0,
        "rows": 0,
        "horizons": [5, 10, 20],
        "skipped": skipped,
        "duration_seconds": round(duration_seconds, 2),
    }


def compute_sector_forecast(trade_date: date | None = None) -> dict[str, Any]:
    """Compute and atomically replace one complete three-horizon sector snapshot."""

    started = monotonic()
    job_now = _job_now()
    with get_session() as session:
        target_date = trade_date or _latest_benchmark_date(session)
        running, stale = _upstream_state(session, now=job_now)
        if running:
            stats = _empty_stats(
                target_date=target_date,
                skipped="upstream_jobs_running",
                duration_seconds=monotonic() - started,
            )
            stats["running_jobs"] = running
            stats["stale_upstream_runs"] = stale
            raise JobExecutionError(
                "上游日线或板块资金流任务仍在运行，板块预测已延后。", stats=stats
            )
        if target_date is None:
            raise JobExecutionError(
                "上证指数交易日历为空，无法确定板块预测日期。",
                stats=_empty_stats(
                    target_date=None,
                    skipped="benchmark_calendar_empty",
                    duration_seconds=monotonic() - started,
                ),
            )
        if trade_date is None and target_date != _market_today():
            stats = _empty_stats(
                target_date=target_date,
                skipped="stale_daily_bars",
                duration_seconds=monotonic() - started,
            )
            stats["expected_date"] = _market_today().isoformat()
            stats["message"] = "目标交易日的日线尚未就绪，本次未重算历史板块预测。"
            return stats

        coverage_before = _input_coverage(session, target_date)
        if not bool(coverage_before["complete"]):
            stats = _empty_stats(
                target_date=target_date,
                skipped="incomplete_daily_bars",
                duration_seconds=monotonic() - started,
            )
            stats["input_coverage"] = coverage_before
            raise JobExecutionError(
                "目标交易日可信全市场日线覆盖不足 80%，板块预测已拒绝计算与写入。",
                stats=stats,
            )

        fingerprint_before = _input_fingerprint(session, target_date)
        result = compute_sector_forecasts(session, target_date)

    with get_session() as session:
        running, stale = _upstream_state(session, now=_job_now())
        fingerprint_after = _input_fingerprint(session, target_date)
        coverage_after = _input_coverage(session, target_date)
        if running or fingerprint_before != fingerprint_after or not bool(
            coverage_after["complete"]
        ):
            stats = _empty_stats(
                target_date=target_date,
                skipped="inputs_changed",
                duration_seconds=monotonic() - started,
            )
            stats["running_jobs"] = running
            stats["stale_upstream_runs"] = stale
            stats["source_fingerprint_before"] = fingerprint_before
            stats["source_fingerprint_after"] = fingerprint_after
            stats["input_coverage"] = coverage_after
            raise JobExecutionError(
                "板块预测计算期间输入发生变化，本次结果已拒绝写入；请稍后重试。",
                stats=stats,
            )
        session.execute(delete(SectorForecast).where(SectorForecast.trade_date == target_date))
        session.execute(insert(SectorForecast), result.rows)

    return {
        **result.stats,
        "skipped": None,
        "stale_upstream_runs": stale,
        "warning_count": len(stale),
        "warnings": (
            ["检测到超出租约的上游 running 审计行，已记录并继续板块预测。"]
            if stale
            else []
        ),
        "source_fingerprint": fingerprint_after,
        "duration_seconds": round(monotonic() - started, 2),
    }


def register_sector_forecast_job() -> None:
    register(
        JobSpec(
            name="sector_forecast",
            func=compute_sector_forecast,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=19,
                minute=50,
                timezone=MARKET_TIMEZONE,
            ),
        )
    )
