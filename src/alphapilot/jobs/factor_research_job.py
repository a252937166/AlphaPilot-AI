from __future__ import annotations

import math
from datetime import date
from time import monotonic
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.backtest.factor_research import (
    _calendar,
    persist_factors_ic,
    research_factors_ic,
)
from alphapilot.data.provenance import AUDITED_SECTOR_FLOW_SOURCES
from alphapilot.db.engine import get_session
from alphapilot.db.models import SectorFlowDaily
from alphapilot.jobs.registry import JobSpec, register

MULTI_YEAR_TRAIN_FACTORS = (
    "momentum_20d",
    "momentum_60d",
    "volatility_20d",
    "turnover_change_5d",
    "pe_percentile",
    "pb_percentile",
)
SECTOR_FLOW_TRAIN_FACTORS = (
    "net_inflow_5d",
)
PRELIMINARY_TRAIN_FACTORS = (
    *MULTI_YEAR_TRAIN_FACTORS,
    *SECTOR_FLOW_TRAIN_FACTORS,
)
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


def _sector_flow_bounds(
    session: Session,
    *,
    end_date: date,
) -> tuple[date, date]:
    row = session.execute(
        select(
            func.min(SectorFlowDaily.trade_date),
            func.max(SectorFlowDaily.trade_date),
        ).where(
            SectorFlowDaily.source.in_(AUDITED_SECTOR_FLOW_SOURCES),
            SectorFlowDaily.trade_date <= end_date,
        )
    ).one()
    if row[0] is None or row[1] is None:
        raise ValueError("sector_flow_daily has no audited history for train-only research")
    return row[0], row[1]


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
    """Run the seven currently available factors on the sealed train window only."""

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
        flow_start, flow_end = _sector_flow_bounds(session, end_date=end_date)
        flow_calendar = _calendar(session, flow_start, flow_end)
        (
            flow_train_start,
            flow_train_end,
            flow_test_start,
            flow_test_end,
            flow_train_size,
        ) = _split_calendar(flow_calendar, ratio)
        flow_table = research_factors_ic(
            session,
            SECTOR_FLOW_TRAIN_FACTORS,
            flow_train_start,
            flow_train_end,
        )
    with get_session() as session:
        persist_factors_ic(
            session,
            multi_year_table,
            sample_tag="train",
            start=multi_year_train_start,
            end=multi_year_train_end,
        )
        persist_factors_ic(
            session,
            flow_table,
            sample_tag="train",
            start=flow_train_start,
            end=flow_train_end,
        )

    multi_year_decisions = sum(
        index + 20 < multi_year_train_size
        for index in range(0, multi_year_train_size, 20)
    )
    flow_decisions = sum(
        index + 20 < flow_train_size for index in range(0, flow_train_size, 20)
    )
    decision_periods = multi_year_decisions + flow_decisions
    duration_seconds = monotonic() - started
    return {
        "status": "preliminary_train_only",
        "sample_tag": "train",
        "factor_scope": "7_of_12_historical_factors",
        "selected_factors": list(PRELIMINARY_TRAIN_FACTORS),
        "pending_financial_factors": list(MISSING_PENDING_FINANCIAL_FACTORS),
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
            "one_year_sector_flow": {
                "factors": list(SECTOR_FLOW_TRAIN_FACTORS),
                "full_calendar": {
                    "start": flow_calendar[0].isoformat(),
                    "end": flow_calendar[-1].isoformat(),
                    "sessions": len(flow_calendar),
                },
                "train_window": {
                    "start": flow_train_start.isoformat(),
                    "end": flow_train_end.isoformat(),
                    "sessions": flow_train_size,
                    "ratio": ratio,
                },
                "sealed_test_window": {
                    "start": flow_test_start.isoformat(),
                    "end": flow_test_end.isoformat(),
                    "sessions": len(flow_calendar) - flow_train_size,
                    "read_factor_outcomes": False,
                },
                "decision_periods": flow_decisions,
                "results": _table_results(flow_table),
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
            "仅 7/12 个历史因子；5 个财务因子等待 S2，非最终 M3 结论。",
            "只计算并落库各 cohort 的 train 样本；test/full 因子结果保持封存。",
            "资金流按其约一年可用区间单独切分，n_periods 显著少于多年价量/估值。",
            "资金流仍严格要求决策时点可见的板块成分；不使用当前成分回填历史映射。",
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
