from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from alphapilot.db.engine import get_session
from alphapilot.db.models import (
    CompositeScore,
    DailyBar,
    FactorValue,
    JobRun,
    Security,
    StyleDaily,
)
from alphapilot.engines.style import (
    StyleAggregationError,
    StyleDailySnapshot,
    compute_style_snapshot,
    style_source_fingerprint,
)
from alphapilot.jobs.registry import JobExecutionError, JobSpec, register

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
MODEL_VERSION = "style-v1.0.0"


def _market_today() -> date:
    return datetime.now(MARKET_TIMEZONE).date()


def _latest_input_dates(session: Session) -> dict[str, date | None]:
    return {
        "daily_bars": session.scalar(select(func.max(DailyBar.trade_date))),
        "composite_scores": session.scalar(select(func.max(CompositeScore.trade_date))),
        "factor_values": session.scalar(select(func.max(FactorValue.trade_date))),
    }


def _input_counts(session: Session, target_date: date) -> dict[str, int]:
    return {
        "daily_bars": int(
            session.scalar(
                select(func.count(func.distinct(DailyBar.symbol))).where(
                    DailyBar.trade_date == target_date
                )
            )
            or 0
        ),
        "composite_scores": int(
            session.scalar(
                select(func.count(func.distinct(CompositeScore.symbol))).where(
                    CompositeScore.trade_date == target_date
                )
            )
            or 0
        ),
        "factor_values": int(
            session.scalar(
                select(func.count(func.distinct(FactorValue.symbol))).where(
                    FactorValue.trade_date == target_date
                )
            )
            or 0
        ),
    }


def _factor_job_running(session: Session) -> bool:
    return bool(
        session.scalar(
            select(func.count())
            .select_from(JobRun)
            .where(JobRun.job_name == "compute_factors", JobRun.status == "running")
        )
    )


def _empty_stats(
    *,
    target_date: date | None,
    skipped: str,
    input_dates: dict[str, date | None],
    input_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "date": target_date.isoformat() if target_date is not None else None,
        "eligible": 0,
        "excluded": 0,
        "counts": {"growth": 0, "value": 0, "defensive": 0, "balanced": 0},
        "weights": {"growth": 0.0, "value": 0.0, "defensive": 0.0, "balanced": 0.0},
        "model_version": MODEL_VERSION,
        "skipped": skipped,
        "input_dates": {
            name: value.isoformat() if value is not None else None
            for name, value in input_dates.items()
        },
        "input_counts": input_counts or {},
    }


def _resolve_target_date(
    session: Session,
    requested_date: date | None,
) -> tuple[date | None, dict[str, date | None], dict[str, int] | None, str | None]:
    input_dates = _latest_input_dates(session)
    if requested_date is not None:
        counts = _input_counts(session, requested_date)
        if not all(counts.values()):
            return requested_date, input_dates, counts, "factor_inputs_not_ready"
        return requested_date, input_dates, counts, None

    latest = set(input_dates.values())
    if None in latest or len(latest) != 1:
        return input_dates["daily_bars"], input_dates, None, "factor_inputs_not_ready"
    target_date = next(iter(latest))
    if target_date != _market_today():
        return target_date, input_dates, None, "stale_inputs"
    return target_date, input_dates, None, None


def _persist_snapshot(
    session: Session,
    target_date: date,
    snapshot: StyleDailySnapshot,
    source_fingerprint: str,
) -> None:
    weights = snapshot.amount_weights
    row = session.get(StyleDaily, target_date)
    values = {
        "growth_pct": float(weights["growth"]),
        "value_pct": float(weights["value"]),
        "defensive_pct": float(weights["defensive"]),
        "balanced_pct": float(weights["balanced"]),
        "model_version": str(snapshot.model_version),
        "source_fingerprint": source_fingerprint,
    }
    if row is None:
        session.add(StyleDaily(trade_date=target_date, **values))
    else:
        for name, value in values.items():
            setattr(row, name, value)


def _refresh_current_tags(session: Session, symbol_tags: Mapping[str, str]) -> None:
    session.execute(update(Security).values(style_tag=None))
    for style_tag in ("growth", "value", "defensive", "balanced"):
        symbols = [symbol for symbol, tag in symbol_tags.items() if tag == style_tag]
        if symbols:
            session.execute(
                update(Security).where(Security.symbol.in_(symbols)).values(style_tag=style_tag)
            )


def compute_style_daily(trade_date: date | None = None) -> dict[str, Any]:
    """Classify one real factor cross-section and persist its market distribution."""

    with get_session() as session:
        input_dates = _latest_input_dates(session)
        if _factor_job_running(session):
            stats = _empty_stats(
                target_date=trade_date or input_dates["daily_bars"],
                skipped="compute_factors_running",
                input_dates=input_dates,
            )
            raise JobExecutionError("因子计算任务仍在运行，风格计算已延后。", stats=stats)

        target_date, input_dates, input_counts, skipped = _resolve_target_date(session, trade_date)
        if skipped is not None:
            return _empty_stats(
                target_date=target_date,
                skipped=skipped,
                input_dates=input_dates,
                input_counts=input_counts,
            )
        assert target_date is not None

        fingerprint_before = style_source_fingerprint(session, target_date)
        try:
            snapshot = compute_style_snapshot(session, target_date)
        except StyleAggregationError as exc:
            stats = _empty_stats(
                target_date=target_date,
                skipped="style_inputs_invalid",
                input_dates=input_dates,
                input_counts=input_counts,
            )
            raise JobExecutionError(str(exc), stats=stats) from exc

        fingerprint_after = style_source_fingerprint(session, target_date)
        final_input_dates = _latest_input_dates(session)
        if (
            _factor_job_running(session)
            or fingerprint_before != fingerprint_after
            or final_input_dates != input_dates
        ):
            stats = _empty_stats(
                target_date=target_date,
                skipped="style_inputs_changed",
                input_dates=final_input_dates,
                input_counts=input_counts,
            )
            stats["source_fingerprint_before"] = fingerprint_before
            stats["source_fingerprint_after"] = fingerprint_after
            raise JobExecutionError(
                "风格计算期间因子输入发生变化，本次结果已拒绝写入；请稍后重试。",
                stats=stats,
            )

        _persist_snapshot(session, target_date, snapshot, fingerprint_after)
        is_latest_snapshot = all(value == target_date for value in final_input_dates.values())
        if is_latest_snapshot:
            _refresh_current_tags(session, snapshot.symbol_tags)

        input_stats = snapshot.input_stats
        return {
            "date": target_date.isoformat(),
            "eligible": int(input_stats.eligible_symbols),
            "excluded": int(input_stats.excluded_symbols),
            "counts": {name: int(value) for name, value in snapshot.tag_counts.items()},
            "weights": {
                name: round(float(value), 12) for name, value in snapshot.amount_weights.items()
            },
            "model_version": str(snapshot.model_version),
            "source_fingerprint": fingerprint_after,
            "skipped": None,
            "input_dates": {
                name: value.isoformat() if value is not None else None
                for name, value in input_dates.items()
            },
            "input_counts": {
                "composite_scores": int(input_stats.composite_symbols),
                "eligible": int(input_stats.eligible_symbols),
                "excluded": int(input_stats.excluded_symbols),
                "missing_security": int(input_stats.missing_security_symbols),
                "missing_or_nonpositive_amount": int(
                    input_stats.missing_or_nonpositive_amount_symbols
                ),
                "factor_coverage": dict(input_stats.factor_coverage),
            },
        }


def register_style_job() -> None:
    register(
        JobSpec(
            name="compute_style_daily",
            func=compute_style_daily,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=19,
                minute=40,
                timezone=MARKET_TIMEZONE,
            ),
        )
    )
