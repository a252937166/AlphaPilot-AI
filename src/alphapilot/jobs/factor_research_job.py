from __future__ import annotations

import math
from datetime import date
from time import monotonic
from typing import Any

from alphapilot.backtest.factor_research import (
    _calendar,
    persist_factors_ic,
    research_factors_ic,
)
from alphapilot.backtest.factor_scope import (
    HISTORICAL_FACTOR_CANDIDATES,
    HISTORY_EXCLUDED_PIT_GAP_FACTORS,
)
from alphapilot.db.engine import get_session
from alphapilot.jobs.registry import JobSpec, register

MULTI_YEAR_TRAIN_FACTORS = (
    "momentum_20d",
    "momentum_60d",
    "volatility_20d",
    "turnover_change_5d",
    "pe_percentile",
    "pb_percentile",
)
PRELIMINARY_TRAIN_FACTORS = MULTI_YEAR_TRAIN_FACTORS
MISSING_PENDING_FINANCIAL_FACTORS = (
    "roe",
    "net_profit_yoy",
    "ocf_to_profit",
    "debt_ratio",
    "revenue_yoy",
)


def _validated_ratio(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("train_ratio must be a finite number within (0, 1)")
    ratio = float(value)
    if not math.isfinite(ratio) or not 0 < ratio < 1:
        raise ValueError("train_ratio must be a finite number within (0, 1)")
    return ratio


def _split_calendar(
    calendar: list[date],
    ratio: float,
) -> tuple[date, date, date, date, int]:
    train_size = int(len(calendar) * ratio)
    if train_size < 2 or len(calendar) - train_size < 2:
        raise ValueError("explicit research window needs at least two train/test sessions")
    return (
        calendar[0],
        calendar[train_size - 1],
        calendar[train_size],
        calendar[-1],
        train_size,
    )


def _table_results(table: Any) -> dict[str, dict[str, Any]]:
    return {
        str(row["factor"]): {
            "ic_mean": row["ic_mean"],
            "ic_ir": row["ic_ir"],
            "t_stat": row["t_stat"],
            "n_periods": int(row["n_periods"]),
            "long_short": row["long_short"],
        }
        for row in table.to_dict(orient="records")
    }


def run_preliminary_train_ic(
    *,
    start_date: date,
    end_date: date,
    train_ratio: float = 0.7,
) -> dict[str, Any]:
    """Run the six currently valid historical factors on the sealed train window.

    The prior seven-factor preview remains an immutable audit artifact, but
    ``net_inflow_5d`` is no longer recomputed or persisted because historical
    constituent membership is not PIT-valid.
    """

    started = monotonic()
    ratio = _validated_ratio(train_ratio)
    with get_session() as session:
        multi_year_calendar = _calendar(session, start_date, end_date)
        (
            multi_year_train_start,
            multi_year_train_end,
            multi_year_test_start,
            multi_year_test_end,
            multi_year_train_size,
        ) = _split_calendar(multi_year_calendar, ratio)
        multi_year_table = research_factors_ic(
            session,
            MULTI_YEAR_TRAIN_FACTORS,
            multi_year_train_start,
            multi_year_train_end,
        )
    with get_session() as session:
        persist_factors_ic(
            session,
            multi_year_table,
            sample_tag="train",
            start=multi_year_train_start,
            end=multi_year_train_end,
        )

    multi_year_decisions = sum(
        index + 20 < multi_year_train_size
        for index in range(0, multi_year_train_size, 20)
    )
    decision_periods = multi_year_decisions
    duration_seconds = monotonic() - started
    return {
        "status": "preliminary_train_only",
        "sample_tag": "train",
        "factor_scope": "6_of_11_historical_factors",
        "historical_factor_candidates": list(HISTORICAL_FACTOR_CANDIDATES),
        "selected_factors": list(PRELIMINARY_TRAIN_FACTORS),
        "pending_financial_factors": list(MISSING_PENDING_FINANCIAL_FACTORS),
        "history_excluded_pit_gap": list(HISTORY_EXCLUDED_PIT_GAP_FACTORS),
        "cohorts": {
            "multi_year_price_valuation": {
                "factors": list(MULTI_YEAR_TRAIN_FACTORS),
                "full_calendar": {
                    "start": multi_year_calendar[0].isoformat(),
                    "end": multi_year_calendar[-1].isoformat(),
                    "sessions": len(multi_year_calendar),
                },
                "train_window": {
                    "start": multi_year_train_start.isoformat(),
                    "end": multi_year_train_end.isoformat(),
                    "sessions": multi_year_train_size,
                    "ratio": ratio,
                },
                "sealed_test_window": {
                    "start": multi_year_test_start.isoformat(),
                    "end": multi_year_test_end.isoformat(),
                    "sessions": len(multi_year_calendar) - multi_year_train_size,
                    "read_factor_outcomes": False,
                },
                "decision_periods": multi_year_decisions,
                "results": _table_results(multi_year_table),
            },
        },
        "decision_periods": decision_periods,
        "duration_seconds": round(duration_seconds, 2),
        "seconds_per_decision_period": (
            round(duration_seconds / decision_periods, 3)
            if decision_periods
            else None
        ),
        "limitations": [
            "仅 6/11 个历史候选因子；5 个财务因子等待 S2，非最终 M3 结论。",
            "只计算并落库各 cohort 的 train 样本；test/full 因子结果保持封存。",
            (
                "net_inflow_5d 因历史成分 PIT 缺口退出 S7/S9；"
                "不使用当前成分回填历史映射。"
            ),
            "未生成或修改任何因子权重。",
        ],
        "test_window_used": False,
        "weights_written": False,
    }


def register_factor_research_job() -> None:
    register(
        JobSpec(
            name="research_preliminary_train_ic",
            func=run_preliminary_train_ic,
            trigger=None,
        )
    )
