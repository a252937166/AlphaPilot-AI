from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, date, datetime, time
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from alphapilot.core.config import get_settings
from alphapilot.db.engine import get_session
from alphapilot.db.models import (
    CompositeScore,
    DailyBar,
    FactorValue,
    JobRun,
    ScoreOutcomeStat,
    Security,
    StockScore,
)
from alphapilot.engines.factors import (
    FACTOR_SET,
    composite,
    compute_factors_for_date,
    load_weights,
    zscore_cross_section,
)
from alphapilot.engines.score_outcomes import (
    HORIZON,
    score_decile,
)
from alphapilot.engines.score_outcomes import (
    MODEL_VERSION as SCORE_OUTCOME_MODEL_VERSION,
)
from alphapilot.engines.stock_score import compute_stock_scores
from alphapilot.jobs.registry import JobExecutionError, JobSpec, register

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
MIN_INPUT_COVERAGE = 0.90
INSERT_BATCH_SIZE = 5_000


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _market_today() -> date:
    return _utc_now().astimezone(MARKET_TIMEZONE).date()


def _finite_or_none(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _market_coverage(session: Session, trade_date: date | None = None) -> dict[str, Any]:
    universe = int(
        session.scalar(
            select(func.count()).select_from(Security).where(Security.list_status == "listed")
        )
        or 0
    )
    target = trade_date
    if target is None:
        target = session.scalar(
            select(func.max(DailyBar.trade_date))
            .select_from(DailyBar)
            .join(Security, Security.symbol == DailyBar.symbol)
            .where(Security.list_status == "listed")
        )
    if not isinstance(target, date):
        return {
            "date": None,
            "universe": universe,
            "eligible": 0,
            "input_coverage": 0.0,
        }
    eligible = int(
        session.scalar(
            select(func.count(func.distinct(DailyBar.symbol)))
            .select_from(DailyBar)
            .join(Security, Security.symbol == DailyBar.symbol)
            .where(
                DailyBar.trade_date == target,
                Security.list_status == "listed",
                Security.is_st.is_(False),
                DailyBar.close > 0,
                DailyBar.volume > 0,
                DailyBar.amount > 0,
            )
        )
        or 0
    )
    return {
        "date": target.isoformat(),
        "universe": universe,
        "eligible": eligible,
        "input_coverage": round(eligible / universe, 6) if universe else 0.0,
    }


def _daily_bars_running(session: Session) -> bool:
    return bool(
        session.scalar(
            select(func.count())
            .select_from(JobRun)
            .where(
                JobRun.job_name == "sync_daily_bars",
                JobRun.status == "running",
            )
        )
    )


def _outcome_win_rates(
    session: Session,
    *,
    trade_date: date,
    score_model_version: str,
) -> dict[int, float]:
    """Load only score calibration that was available by the target date."""

    scheduled_cutoff = datetime.combine(
        trade_date,
        time(19, 30),
        tzinfo=MARKET_TIMEZONE,
    ).astimezone(UTC)
    now = _utc_now()
    availability_cutoff = (
        now if trade_date == now.astimezone(MARKET_TIMEZONE).date() else scheduled_cutoff
    )
    rows = session.scalars(
        select(ScoreOutcomeStat).where(
            ScoreOutcomeStat.score_model_version == score_model_version,
            ScoreOutcomeStat.model_version == SCORE_OUTCOME_MODEL_VERSION,
            ScoreOutcomeStat.horizon == HORIZON,
            ScoreOutcomeStat.as_of_date <= trade_date,
            ScoreOutcomeStat.samples > 0,
            ScoreOutcomeStat.win_rate.is_not(None),
        )
    ).all()
    rates: dict[int, float] = {}
    for row in rows:
        updated_at = row.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        if updated_at.astimezone(UTC) > availability_cutoff:
            continue
        value = _finite_or_none(row.win_rate)
        if value is not None and 0.0 <= value <= 1.0:
            rates[row.decile] = value
    return rates


def _factor_records(
    raw: pd.DataFrame,
    standardized: pd.DataFrame,
    *,
    trade_date: date,
    model_version: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for symbol in raw.index:
        code = str(symbol)
        for factor in FACTOR_SET:
            records.append(
                {
                    "symbol": code,
                    "trade_date": trade_date,
                    "factor": factor,
                    "raw": _finite_or_none(raw.at[symbol, factor]),
                    "zscore": _finite_or_none(standardized.at[symbol, factor]),
                    "model_version": model_version,
                }
            )
    return records


def _composite_records(
    standardized: pd.DataFrame,
    scores: pd.Series,
    *,
    trade_date: date,
    model_version: str,
    win_rates_by_decile: Mapping[int, float],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for symbol, raw_score in scores.items():
        score = _finite_or_none(raw_score)
        if score is None:
            continue
        factors = {
            factor: _finite_or_none(standardized.at[symbol, factor]) for factor in FACTOR_SET
        }
        records.append(
            {
                "symbol": str(symbol),
                "trade_date": trade_date,
                "score": score,
                "win_rate_20d": win_rates_by_decile.get(score_decile(score)),
                "factors": factors,
                "model_version": model_version,
            }
        )
    return records


def _stock_score_records(
    scores: pd.DataFrame,
    *,
    trade_date: date,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    numeric_fields = (
        "tech",
        "capital",
        "fundamental",
        "valuation",
        "sentiment",
        "composite",
    )
    for symbol, row in scores.iterrows():
        values = {field: _finite_or_none(row[field]) for field in numeric_fields}
        if any(value is None for value in values.values()):
            raise ValueError(f"{symbol} 的个股五维评分包含非有限值。")
        records.append(
            {
                "symbol": str(symbol),
                "trade_date": trade_date,
                **values,
                "model_version": str(row["model_version"]),
            }
        )
    return records


def _insert_batches(
    session: Session,
    model: type[FactorValue] | type[CompositeScore] | type[StockScore],
    records: list[dict[str, Any]],
) -> None:
    for offset in range(0, len(records), INSERT_BATCH_SIZE):
        session.execute(insert(model), records[offset : offset + INSERT_BATCH_SIZE])


def compute_factors(trade_date: date | None = None) -> dict[str, Any]:
    """Compute and atomically replace one complete daily factor cross-section."""

    started = monotonic()
    settings = get_settings()
    weight_config = load_weights(settings.factor_weights_file)
    factor_model_version = f"factor-{weight_config.version}"
    score_model_version = f"factor-score-{weight_config.version}"

    with get_session() as session:
        readiness = _market_coverage(session, trade_date)
        if _daily_bars_running(session):
            raise JobExecutionError(
                "日线同步任务仍在运行，因子计算已延后。",
                stats={**readiness, "reason": "daily_bars_running"},
            )
        market_today = _market_today()
        if (
            trade_date is None
            and readiness["date"] is not None
            and readiness["date"] != market_today.isoformat()
        ):
            return {
                **readiness,
                "skipped": "stale_daily_bars",
                "expected_date": market_today.isoformat(),
                "message": "目标交易日的日线尚未就绪，本次未重算历史评分。",
                "duration_seconds": round(monotonic() - started, 2),
            }
        if readiness["date"] is None or float(readiness["input_coverage"]) < MIN_INPUT_COVERAGE:
            raise JobExecutionError(
                "因子输入覆盖率低于 90% 安全阈值。",
                stats={**readiness, "reason": "input_coverage_below_floor"},
            )

        target_date = date.fromisoformat(str(readiness["date"]))
        raw = compute_factors_for_date(session, target_date)
        if raw.empty:
            raise JobExecutionError(
                f"{target_date.isoformat()} 没有可评分证券，因子计算已停止。",
                stats={**readiness, "reason": "empty_factor_frame"},
            )
        standardized = zscore_cross_section(raw)
        scores = composite(standardized, weight_config.weights)
        outcome_win_rates = _outcome_win_rates(
            session,
            trade_date=target_date,
            score_model_version=score_model_version,
        )
        factor_records = _factor_records(
            raw,
            standardized,
            trade_date=target_date,
            model_version=factor_model_version,
        )
        composite_records = _composite_records(
            standardized,
            scores,
            trade_date=target_date,
            model_version=score_model_version,
            win_rates_by_decile=outcome_win_rates,
        )
        stock_score_frame = compute_stock_scores(standardized)
        stock_score_records = _stock_score_records(
            stock_score_frame,
            trade_date=target_date,
        )
        if len(composite_records) / int(readiness["universe"]) < MIN_INPUT_COVERAGE:
            raise JobExecutionError(
                "综合评分覆盖率低于 90% 完成阈值。",
                stats={
                    **readiness,
                    "symbols": len(composite_records),
                    "reason": "composite_coverage_below_floor",
                },
            )
        if len(stock_score_records) != len(composite_records):
            raise JobExecutionError(
                "个股五维评分与综合评分行数不一致，原子写入已停止。",
                stats={
                    **readiness,
                    "composite_rows": len(composite_records),
                    "stock_score_rows": len(stock_score_records),
                    "reason": "stock_score_row_mismatch",
                },
            )

        session.execute(delete(FactorValue).where(FactorValue.trade_date == target_date))
        session.execute(delete(CompositeScore).where(CompositeScore.trade_date == target_date))
        session.execute(delete(StockScore).where(StockScore.trade_date == target_date))
        _insert_batches(session, FactorValue, factor_records)
        _insert_batches(session, CompositeScore, composite_records)
        _insert_batches(session, StockScore, stock_score_records)

    coverage = {
        factor: {
            "count": int(raw[factor].notna().sum()),
            "ratio": round(float(raw[factor].notna().mean()), 6),
        }
        for factor in FACTOR_SET
    }
    active_weights = {
        factor: weight
        for factor, weight in weight_config.weights.items()
        if factor in standardized
        and bool(standardized[factor].notna().any())
        and bool((standardized[factor].abs() > 1e-12).any())
    }
    top5 = [
        {"symbol": str(symbol), "score": round(float(score), 6)}
        for symbol, score in scores.sort_values(ascending=False).head(5).items()
    ]
    return {
        **readiness,
        "symbols": len(composite_records),
        "factor_rows": len(factor_records),
        "composite_rows": len(composite_records),
        "stock_score_rows": len(stock_score_records),
        "stock_score_model_versions": sorted(
            {str(record["model_version"]) for record in stock_score_records}
        ),
        "stock_score_factor_coverage": stock_score_frame.attrs["factor_coverage"],
        "stock_score_full_rows": stock_score_frame.attrs["full_rows"],
        "stock_score_neutral_rows": stock_score_frame.attrs["neutral_rows"],
        "coverage": coverage,
        "sector_flow_days": int(raw.attrs.get("sector_flow_days", 0)),
        "active_weights": active_weights,
        "model_version": score_model_version,
        "outcome_win_rate_deciles": sorted(outcome_win_rates),
        "top5": top5,
        "duration_seconds": round(monotonic() - started, 2),
    }


def register_factor_job() -> None:
    register(
        JobSpec(
            name="compute_factors",
            func=compute_factors,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=19,
                minute=30,
                timezone=MARKET_TIMEZONE,
            ),
        )
    )
