from __future__ import annotations

import hashlib
from datetime import date, datetime
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from alphapilot.db.engine import get_session
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
UPSTREAM_JOBS = ("sync_daily_bars", "sync_sector_flows")


def _market_today() -> date:
    return datetime.now(MARKET_TIMEZONE).date()


def _upstream_running(session: Session) -> list[str]:
    return list(
        session.scalars(
            select(JobRun.job_name)
            .where(
                JobRun.job_name.in_(UPSTREAM_JOBS),
                JobRun.status == "running",
            )
            .distinct()
            .order_by(JobRun.job_name)
        ).all()
    )


def _latest_benchmark_date(session: Session) -> date | None:
    value = session.scalar(
        select(func.max(DailyBar.trade_date)).where(DailyBar.symbol == "SH.000001")
    )
    return value if isinstance(value, date) else None


def _input_fingerprint(session: Session, target_date: date) -> str:
    dates = list(
        session.scalars(
            select(DailyBar.trade_date)
            .where(
                DailyBar.symbol == "SH.000001",
                DailyBar.trade_date <= target_date,
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
        ).where(SectorFlowDaily.trade_date.in_(dates))
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
    with get_session() as session:
        target_date = trade_date or _latest_benchmark_date(session)
        running = _upstream_running(session)
        if running:
            stats = _empty_stats(
                target_date=target_date,
                skipped="upstream_jobs_running",
                duration_seconds=monotonic() - started,
            )
            stats["running_jobs"] = running
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

        fingerprint_before = _input_fingerprint(session, target_date)
        result = compute_sector_forecasts(session, target_date)

    with get_session() as session:
        running = _upstream_running(session)
        fingerprint_after = _input_fingerprint(session, target_date)
        if running or fingerprint_before != fingerprint_after:
            stats = _empty_stats(
                target_date=target_date,
                skipped="inputs_changed",
                duration_seconds=monotonic() - started,
            )
            stats["running_jobs"] = running
            stats["source_fingerprint_before"] = fingerprint_before
            stats["source_fingerprint_after"] = fingerprint_after
            raise JobExecutionError(
                "板块预测计算期间输入发生变化，本次结果已拒绝写入；请稍后重试。",
                stats=stats,
            )
        session.execute(delete(SectorForecast).where(SectorForecast.trade_date == target_date))
        session.execute(insert(SectorForecast), result.rows)

    return {
        **result.stats,
        "skipped": None,
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
