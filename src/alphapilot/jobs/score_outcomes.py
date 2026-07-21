from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from math import isfinite
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import Session, aliased

from alphapilot.db.engine import get_session
from alphapilot.db.models import (
    CompositeScore,
    DailyBar,
    JobRun,
    ScoreOutcomeStat,
    utcnow,
)
from alphapilot.engines.score_outcomes import (
    HORIZON,
    MODEL_VERSION,
    OutcomeBucket,
    aggregate_outcomes,
    nondecreasing_rates,
    score_decile,
)
from alphapilot.jobs.registry import JobExecutionError, JobSpec, register

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
INDEX_SYMBOL = "SH.000001"
CLOSE_END = time(15, 6)
UPSTREAM_JOBS = ("sync_daily_bars", "compute_factors")


def _as_utc(value: datetime) -> datetime:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).astimezone(UTC)


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _evaluation_time(cutoff: date | None) -> datetime:
    if cutoff is None:
        return datetime.now(UTC)
    # Explicit historical runs are evaluated with data available by T+1 close.
    return datetime.combine(
        cutoff + timedelta(days=1),
        time.max,
        tzinfo=MARKET_TIMEZONE,
    ).astimezone(UTC)


def _complete_close(
    close: object,
    trade_date: date,
    ingested_at: datetime | None,
    *,
    as_of: datetime,
) -> float | None:
    value = _finite_float(close)
    if value is None or value <= 0 or not isinstance(ingested_at, datetime):
        return None
    ingested_utc = _as_utc(ingested_at)
    if ingested_utc > as_of:
        return None
    local = ingested_utc.astimezone(MARKET_TIMEZONE)
    complete_same_day = local.date() == trade_date and local.time() >= CLOSE_END
    if trade_date >= local.date() and not complete_same_day:
        return None
    return value


def _running_upstreams(session: Session) -> list[str]:
    return sorted(
        set(
            session.scalars(
                select(JobRun.job_name).where(
                    JobRun.job_name.in_(UPSTREAM_JOBS),
                    JobRun.status == "running",
                )
            ).all()
        )
    )


def _target_context(
    session: Session,
    cutoff: date | None,
) -> tuple[date, str] | None:
    target_query = select(func.max(CompositeScore.trade_date))
    if cutoff is not None:
        target_query = target_query.where(CompositeScore.trade_date <= cutoff)
    target = session.scalar(target_query)
    if not isinstance(target, date):
        return None
    versions = list(
        session.scalars(
            select(CompositeScore.model_version)
            .where(CompositeScore.trade_date == target)
            .distinct()
            .order_by(CompositeScore.model_version)
        ).all()
    )
    if len(versions) != 1:
        raise JobExecutionError(
            f"{target.isoformat()} 的综合评分包含多个模型版本，胜率评估已停止。",
            stats={
                "date": target.isoformat(),
                "model_versions": versions,
                "reason": "mixed_score_model_versions",
            },
        )
    return target, versions[0]


def _trading_calendar(
    session: Session,
    *,
    target: date,
    as_of: datetime,
) -> list[date]:
    rows = session.execute(
        select(DailyBar.trade_date, DailyBar.ingested_at)
        .where(
            DailyBar.symbol == INDEX_SYMBOL,
            DailyBar.trade_date <= target,
        )
        .order_by(DailyBar.trade_date)
    ).all()
    return sorted(
        {
            trade_date
            for trade_date, ingested_at in rows
            if isinstance(trade_date, date)
            and isinstance(ingested_at, datetime)
            and _as_utc(ingested_at) <= as_of
        }
    )


def _guard_against_state_rollback(
    session: Session,
    *,
    target: date,
    score_model_version: str,
) -> None:
    newest = session.scalar(
        select(func.max(ScoreOutcomeStat.as_of_date)).where(
            ScoreOutcomeStat.score_model_version == score_model_version,
            ScoreOutcomeStat.horizon == HORIZON,
        )
    )
    if isinstance(newest, date) and newest > target:
        raise JobExecutionError(
            "已有更新日期更晚的胜率统计，历史评估不得覆盖当前状态。",
            stats={
                "date": target.isoformat(),
                "newest_outcome_date": newest.isoformat(),
                "reason": "outcome_state_newer_than_target",
            },
        )


def _mature_pairs(
    session: Session,
    *,
    calendar: list[date],
    score_model_version: str,
) -> list[tuple[date, date]]:
    positions = {trade_date: index for index, trade_date in enumerate(calendar)}
    score_dates = session.scalars(
        select(CompositeScore.trade_date)
        .where(CompositeScore.model_version == score_model_version)
        .distinct()
        .order_by(CompositeScore.trade_date)
    ).all()
    pairs: list[tuple[date, date]] = []
    for score_date in score_dates:
        position = positions.get(score_date)
        if position is None or position + HORIZON >= len(calendar):
            continue
        pairs.append((score_date, calendar[position + HORIZON]))
    return pairs


def _outcome_rows(
    session: Session,
    *,
    pairs: list[tuple[date, date]],
    score_model_version: str,
    as_of: datetime,
) -> list[tuple[object, object, object]]:
    origin_bar = aliased(DailyBar, name="score_origin_bar")
    maturity_bar = aliased(DailyBar, name="score_maturity_bar")
    rows: list[tuple[object, object, object]] = []
    for origin_date, maturity_date in pairs:
        observations = session.execute(
            select(
                CompositeScore.score,
                origin_bar.close,
                origin_bar.ingested_at,
                maturity_bar.close,
                maturity_bar.ingested_at,
            )
            .outerjoin(
                origin_bar,
                and_(
                    origin_bar.symbol == CompositeScore.symbol,
                    origin_bar.trade_date == origin_date,
                ),
            )
            .outerjoin(
                maturity_bar,
                and_(
                    maturity_bar.symbol == CompositeScore.symbol,
                    maturity_bar.trade_date == maturity_date,
                ),
            )
            .where(
                CompositeScore.trade_date == origin_date,
                CompositeScore.model_version == score_model_version,
            )
            .order_by(CompositeScore.symbol)
        ).all()
        for score, origin_close, origin_ingested, maturity_close, maturity_ingested in observations:
            rows.append(
                (
                    score,
                    _complete_close(
                        origin_close,
                        origin_date,
                        origin_ingested,
                        as_of=as_of,
                    ),
                    _complete_close(
                        maturity_close,
                        maturity_date,
                        maturity_ingested,
                        as_of=as_of,
                    ),
                )
            )
    return rows


def _persist_buckets(
    session: Session,
    *,
    buckets: tuple[OutcomeBucket, ...],
    target: date,
    score_model_version: str,
) -> None:
    existing = {
        row.decile: row
        for row in session.scalars(
            select(ScoreOutcomeStat).where(
                ScoreOutcomeStat.score_model_version == score_model_version,
                ScoreOutcomeStat.horizon == HORIZON,
            )
        ).all()
    }
    updated_at = utcnow()
    for bucket in buckets:
        row = existing.get(bucket.decile)
        if row is None:
            row = ScoreOutcomeStat(
                decile=bucket.decile,
                horizon=HORIZON,
                score_model_version=score_model_version,
                as_of_date=target,
            )
            session.add(row)
        row.samples = bucket.samples
        row.positive_samples = bucket.positive_samples
        row.win_rate = bucket.win_rate
        row.model_version = MODEL_VERSION
        row.as_of_date = target
        row.updated_at = updated_at


def _update_current_scores(
    session: Session,
    *,
    target: date,
    score_model_version: str,
    buckets: tuple[OutcomeBucket, ...],
) -> tuple[int, int, int]:
    current = session.scalars(
        select(CompositeScore).where(
            CompositeScore.trade_date == target,
            CompositeScore.model_version == score_model_version,
        )
    ).all()
    session.execute(
        update(CompositeScore)
        .where(
            CompositeScore.trade_date == target,
            CompositeScore.model_version == score_model_version,
        )
        .values(win_rate_20d=None)
    )
    rates = {bucket.decile: bucket.win_rate for bucket in buckets if bucket.win_rate is not None}
    assigned = 0
    invalid = 0
    for row in current:
        try:
            win_rate = rates.get(score_decile(row.score))
        except ValueError:
            invalid += 1
            continue
        if win_rate is None:
            continue
        row.win_rate_20d = win_rate
        assigned += 1
    return len(current), assigned, invalid


def _bucket_payload(bucket: OutcomeBucket) -> dict[str, int | float | None]:
    return {
        "decile": bucket.decile,
        "samples": bucket.samples,
        "positive_samples": bucket.positive_samples,
        "win_rate": bucket.win_rate,
    }


def evaluate_scores(as_of_date: date | None = None) -> dict[str, Any]:
    """Evaluate exact 20-session score outcomes and refresh current hit rates."""

    started = monotonic()
    evaluated_at = _evaluation_time(as_of_date)
    with get_session() as session:
        running = _running_upstreams(session)
        if running:
            raise JobExecutionError(
                "日线同步或因子计算任务仍在运行，胜率评估已延后。",
                stats={"running_jobs": running, "reason": "upstream_job_running"},
            )
        context = _target_context(session, as_of_date)
        if context is None:
            return {
                "date": None,
                "skipped": "no_composite_scores",
                "message": "没有可评估的综合评分，未生成胜率统计。",
                "duration_seconds": round(monotonic() - started, 2),
            }
        target, score_model_version = context
        _guard_against_state_rollback(
            session,
            target=target,
            score_model_version=score_model_version,
        )
        calendar = _trading_calendar(
            session,
            target=target,
            as_of=evaluated_at,
        )
        if target not in calendar:
            raise JobExecutionError(
                "目标评分日不在上证指数交易日历中，胜率评估已停止。",
                stats={
                    "date": target.isoformat(),
                    "calendar_sessions": len(calendar),
                    "reason": "target_missing_from_calendar",
                },
            )
        pairs = _mature_pairs(
            session,
            calendar=calendar,
            score_model_version=score_model_version,
        )
        rows = _outcome_rows(
            session,
            pairs=pairs,
            score_model_version=score_model_version,
            as_of=evaluated_at,
        )
        aggregate = aggregate_outcomes(rows)
        _persist_buckets(
            session,
            buckets=aggregate.buckets,
            target=target,
            score_model_version=score_model_version,
        )
        current_rows, assigned_rows, invalid_current_rows = _update_current_scores(
            session,
            target=target,
            score_model_version=score_model_version,
            buckets=aggregate.buckets,
        )

    return {
        "date": target.isoformat(),
        "score_model_version": score_model_version,
        "model_version": MODEL_VERSION,
        "horizon": HORIZON,
        "calendar_sessions": len(calendar),
        "mature_score_dates": len(pairs),
        "input_rows": aggregate.input_rows,
        "evaluated_rows": aggregate.evaluated_rows,
        "missing_endpoint_rows": aggregate.missing_endpoint_rows,
        "invalid_score_rows": aggregate.invalid_score_rows,
        "current_rows": current_rows,
        "current_assigned_rows": assigned_rows,
        "current_invalid_rows": invalid_current_rows,
        "nondecreasing": nondecreasing_rates(aggregate.buckets),
        "buckets": [_bucket_payload(bucket) for bucket in aggregate.buckets],
        "duration_seconds": round(monotonic() - started, 2),
    }


def register_score_outcomes_job() -> None:
    register(
        JobSpec(
            name="evaluate_scores",
            func=evaluate_scores,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=20,
                minute=0,
                timezone=MARKET_TIMEZONE,
            ),
        )
    )
