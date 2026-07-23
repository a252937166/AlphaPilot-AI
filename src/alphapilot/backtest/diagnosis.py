from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.backtest.factor_research import classify_factors
from alphapilot.backtest.report import generate_report
from alphapilot.db.models import (
    BacktestDaily,
    BacktestRun,
    FactorCorrelationStat,
    FactorICStat,
)
from alphapilot.engines.factors import FACTOR_SET, load_weights

_ROOT = Path(__file__).resolve().parents[3]
_V1_WEIGHTS = _ROOT / "config" / "factor_weights.yaml"
_V2_WEIGHTS = _ROOT / "config" / "factor_weights_v2.yaml"
_SAMPLE_TAGS = frozenset({"train", "test", "full"})

_DIRECTION_AUDITS: dict[str, dict[str, Any]] = {
    "momentum_20d": {
        "formula": "adj_close[T] / adj_close[T-20] - 1",
        "raw_direction": "上涨为正",
        "verdict": "公式与技术趋势定义一致；当前弱负 IC 不构成符号 bug。",
        "bug_found": False,
    },
    "momentum_60d": {
        "formula": "adj_close[T] / adj_close[T-60] - 1",
        "raw_direction": "上涨为正",
        "verdict": "公式正确；v1 未赋权，不是 v1 反向主因。",
        "bug_found": False,
    },
    "volatility_20d": {
        "formula": "std(adj_close.pct_change, 20) × sqrt(252)",
        "raw_direction": "风险越高值越大",
        "verdict": "风险量定义正确；负 IC 符合低波动偏好，应由权重表达。",
        "bug_found": False,
    },
    "turnover_change_5d": {
        "formula": "mean(amount[-5:]) / mean(amount[-10:-5]) - 1",
        "raw_direction": "活跃度升温为正",
        "verdict": "代理口径与 P2.2 契约一致；弱负结果不是符号 bug。",
        "bug_found": False,
    },
    "net_inflow_5d": {
        "formula": "sum(sector_net_inflow, 5d)",
        "raw_direction": "净流入为正",
        "verdict": "方向正确；历史截面不足，禁止回填。",
        "bug_found": False,
    },
    "roe": {
        "formula": "latest_disclosed_roe",
        "raw_direction": "盈利效率越高值越大",
        "verdict": "方向正确；当前横截面覆盖不足。",
        "bug_found": False,
    },
    "net_profit_yoy": {
        "formula": "latest_disclosed_net_profit_yoy",
        "raw_direction": "增长为正",
        "verdict": "方向正确；当前历史 PIT 覆盖不足。",
        "bug_found": False,
    },
    "ocf_to_profit": {
        "formula": "latest_disclosed_operating_cash_flow / profit",
        "raw_direction": "现金质量越高值越大",
        "verdict": "方向正确；当前历史 PIT 覆盖不足。",
        "bug_found": False,
    },
    "debt_ratio": {
        "formula": "latest_disclosed_debt_ratio",
        "raw_direction": "杠杆越高值越大",
        "verdict": "原始风险量无需反号；偏好应由负权表达。",
        "bug_found": False,
    },
    "revenue_yoy": {
        "formula": "latest_disclosed_revenue_yoy",
        "raw_direction": "增长为正",
        "verdict": "方向正确；当前历史 PIT 覆盖不足。",
        "bug_found": False,
    },
    "pe_percentile": {
        "formula": "cross_sectional_percentile(positive_pe)",
        "raw_direction": "越贵值越大",
        "verdict": "原始估值方向正确；便宜偏好由 v1 负权表达。",
        "bug_found": False,
    },
    "pb_percentile": {
        "formula": "cross_sectional_percentile(positive_pb)",
        "raw_direction": "越贵值越大",
        "verdict": "原始估值方向正确；便宜偏好应由负权表达。",
        "bug_found": False,
    },
    "sector_strength": {
        "formula": "latest_sector_strength_at_decision_time",
        "raw_direction": "板块强度越高值越大",
        "verdict": "方向正确；历史快照不足，禁止回填。",
        "bug_found": False,
    },
}


def _finite(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _latest_ic_window(
    session: Session,
    sample_tag: Literal["train", "test", "full"],
) -> tuple[date, date] | None:
    row = session.execute(
        select(FactorICStat.start_date, FactorICStat.end_date)
        .where(FactorICStat.sample_tag == sample_tag)
        .order_by(
            FactorICStat.end_date.desc(),
            FactorICStat.start_date.desc(),
            FactorICStat.updated_at.desc(),
        )
        .limit(1)
    ).first()
    return (row[0], row[1]) if row is not None else None


def factor_ic_report(
    session: Session,
    sample_tag: Literal["train", "test", "full"] = "full",
) -> dict[str, Any]:
    """Load the latest persisted single-factor evidence without recomputation."""

    if sample_tag not in _SAMPLE_TAGS:
        raise ValueError("sample_tag must be one of train/test/full")
    window = _latest_ic_window(session, sample_tag)
    stored: dict[str, FactorICStat] = {}
    if window is not None:
        start, end = window
        rows = session.scalars(
            select(FactorICStat).where(
                FactorICStat.sample_tag == sample_tag,
                FactorICStat.start_date == start,
                FactorICStat.end_date == end,
            )
        )
        stored = {row.factor: row for row in rows}
    factors: list[dict[str, Any]] = []
    for factor in FACTOR_SET:
        row = stored.get(factor)
        factors.append(
            {
                "factor": factor,
                "ic_mean": _finite(row.ic_mean) if row is not None else None,
                "ic_ir": _finite(row.ic_ir) if row is not None else None,
                "t_stat": _finite(row.t_stat) if row is not None else None,
                "ic_positive_ratio": (_finite(row.ic_positive_ratio) if row is not None else None),
                "long_short": _finite(row.long_short) if row is not None else None,
                "n_periods": row.n_periods if row is not None else 0,
            }
        )
    available_count = sum(item["n_periods"] > 0 for item in factors)
    return {
        "available": window is not None,
        "sample_tag": sample_tag,
        "start_date": window[0].isoformat() if window is not None else None,
        "end_date": window[1].isoformat() if window is not None else None,
        "factor_count": len(FACTOR_SET),
        "available_count": available_count,
        "factors": factors,
        "limitations": [
            "仅使用持久化的严格 PIT 研究结果；接口不会现场补算或回填缺失历史。",
            f"{len(FACTOR_SET)} 个运行时因子中仅 {available_count} 个有可测截面。",
        ],
    }


def _correlation_report(
    session: Session,
    *,
    sample_tag: Literal["train", "test", "full"],
    window: tuple[date, date] | None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    matrix = pd.DataFrame(
        float("nan"),
        index=FACTOR_SET,
        columns=FACTOR_SET,
        dtype=float,
    )
    counts = pd.DataFrame(
        0,
        index=FACTOR_SET,
        columns=FACTOR_SET,
        dtype=int,
    )
    if window is not None:
        rows = list(
            session.scalars(
                select(FactorCorrelationStat).where(
                    FactorCorrelationStat.sample_tag == sample_tag,
                    FactorCorrelationStat.start_date == window[0],
                    FactorCorrelationStat.end_date == window[1],
                )
            )
        )
        for row in rows:
            if row.left_factor not in FACTOR_SET or row.right_factor not in FACTOR_SET:
                continue
            matrix.at[row.left_factor, row.right_factor] = row.correlation
            matrix.at[row.right_factor, row.left_factor] = row.correlation
            counts.at[row.left_factor, row.right_factor] = row.n_periods
            counts.at[row.right_factor, row.left_factor] = row.n_periods
    redundant_pairs: list[dict[str, Any]] = []
    for left_index, left in enumerate(FACTOR_SET):
        for right in FACTOR_SET[left_index + 1 :]:
            value = _finite(matrix.at[left, right])
            if value is not None and abs(value) > 0.8:
                redundant_pairs.append(
                    {
                        "left": left,
                        "right": right,
                        "correlation": value,
                        "n_periods": int(str(counts.at[left, right])),
                    }
                )
    values = [[_finite(matrix.at[left, right]) for right in FACTOR_SET] for left in FACTOR_SET]
    period_values = [
        [int(str(counts.at[left, right])) for right in FACTOR_SET] for left in FACTOR_SET
    ]
    available_cells = sum(value is not None for row in values for value in row)
    return matrix, {
        "available": available_cells > 0,
        "method": "mean_cross_sectional_pearson",
        "minimum_pair_periods": 3,
        "threshold": 0.8,
        "factors": list(FACTOR_SET),
        "values": values,
        "n_periods": period_values,
        "available_cells": available_cells,
        "redundant_pairs": redundant_pairs,
        "limitation": ("灰色单元格代表不足 3 个有效决策截面，不按 0 处理。"),
    }


def _weight_report() -> dict[str, Any]:
    v1 = load_weights(_V1_WEIGHTS)
    v2 = load_weights(_V2_WEIGHTS)
    v1_weights = {factor: float(v1.weights.get(factor, 0.0)) for factor in FACTOR_SET}
    v2_weights = {factor: float(v2.weights.get(factor, 0.0)) for factor in FACTOR_SET}
    return {
        "factors": list(FACTOR_SET),
        "v1": {
            "version": v1.version,
            "profile": v1.profile,
            "weights": v1_weights,
        },
        "v2": {
            "version": v2.version,
            "profile": v2.profile,
            "weights": v2_weights,
        },
        "delta": {factor: v2_weights[factor] - v1_weights[factor] for factor in FACTOR_SET},
        "method": "single_train_window_signed_ic_ir_l1",
        "test_window_used_for_weights": False,
    }


def factor_diagnosis_report(
    session: Session,
    sample_tag: Literal["train", "test", "full"] = "full",
) -> dict[str, Any]:
    """Build the complete M2 factor report from frozen research artifacts."""

    ic = factor_ic_report(session, sample_tag)
    window = (
        (date.fromisoformat(ic["start_date"]), date.fromisoformat(ic["end_date"]))
        if ic["start_date"] is not None and ic["end_date"] is not None
        else None
    )
    corr_frame, correlation = _correlation_report(
        session,
        sample_tag=sample_tag,
        window=window,
    )
    ic_frame = pd.DataFrame(ic["factors"])
    classification = classify_factors(ic_frame, corr_frame)
    classified = classification["factors"]
    factors = [
        {
            **item,
            **classified[item["factor"]],
            "direction_audit": _DIRECTION_AUDITS[item["factor"]],
        }
        for item in ic["factors"]
    ]
    return {
        "available": ic["available"],
        "sample": {
            "tag": sample_tag,
            "start_date": ic["start_date"],
            "end_date": ic["end_date"],
            "factor_count": ic["factor_count"],
            "available_count": ic["available_count"],
            "evidence_label": "约 1 年样本 · 弱证据 · 仅作方向性参考",
        },
        "factors": factors,
        "classification_counts": {
            label: sum(item["classification"] == label for item in factors)
            for label in (
                "significant_positive",
                "significant_reverse",
                "ineffective",
                "insufficient_data",
            )
        },
        "correlation": correlation,
        "redundancy_groups": classification["redundancy_groups"],
        "weights": _weight_report(),
        "source_audit": {
            "factor_source": "engines/factors.py",
            "audited_factor_count": len(_DIRECTION_AUDITS),
            "calculation_bug_found": False,
            "verdict": "13 个因子公式方向已逐项审计，未发现符号实现错误。",
        },
        "conclusion": {
            "status": "weak_or_insufficient_evidence",
            "headline": "因子方向重构不等于策略成功；样本外结果仍须单独判定。",
            "policy": ("不使用 test 窗调权，不把缺失因子当作零 IC，不因当前结果不理想而重复试参。"),
        },
        "limitations": [
            *ic["limitations"],
            correlation["limitation"],
            "约 1 年数据不足以覆盖完整牛熊周期，统计结果只作方向性参考。",
            "9 个因子缺乏历史 PIT 横截面，当前诊断主要反映 4 个价量因子。",
        ],
    }


def _protocol_mismatch(v1: BacktestRun, v2: BacktestRun) -> list[str]:
    mismatch: list[str] = []
    fields = ("start_date", "end_date", "rebalance_freq", "top_pct")
    for field in fields:
        if getattr(v1, field) != getattr(v2, field):
            mismatch.append(field)
    for field in ("initial_capital", "cost_model", "execution"):
        if v1.params.get(field) != v2.params.get(field):
            mismatch.append(f"params.{field}")
    return mismatch


def _comparison_curve(
    session: Session,
    v1: BacktestRun,
    v2: BacktestRun,
) -> dict[str, Any]:
    v1_rows = list(
        session.scalars(
            select(BacktestDaily)
            .where(BacktestDaily.run_id == v1.id)
            .order_by(BacktestDaily.trade_date)
        )
    )
    v2_rows = list(
        session.scalars(
            select(BacktestDaily)
            .where(BacktestDaily.run_id == v2.id)
            .order_by(BacktestDaily.trade_date)
        )
    )
    if [row.trade_date for row in v1_rows] != [row.trade_date for row in v2_rows]:
        raise ValueError("v1/v2 daily trading calendars do not match")
    return {
        "dates": [row.trade_date.isoformat() for row in v1_rows],
        "v1_nav": [row.nav for row in v1_rows],
        "v2_nav": [row.nav for row in v2_rows],
        "csi300_nav": [row.benchmark_nav for row in v2_rows],
        "market_nav": [row.market_nav for row in v2_rows],
    }


def compare_backtests(
    session: Session,
    v1_id: int,
    v2_id: int,
) -> dict[str, Any]:
    """Compare frozen v1/v2 test runs without any parameter search."""

    v1 = session.get(BacktestRun, v1_id)
    v2 = session.get(BacktestRun, v2_id)
    if v1 is None or v2 is None:
        raise ValueError("v1 or v2 backtest run does not exist")
    if v1.signal_id != "composite-v1" or v2.signal_id != "composite-v2":
        raise ValueError("v1/v2 parameters must reference composite-v1/composite-v2")
    if v1.status != "completed" or v2.status != "completed":
        raise ValueError("v1/v2 backtest runs must both be completed")
    mismatch = _protocol_mismatch(v1, v2)
    if mismatch:
        raise ValueError(f"v1/v2 protocol mismatch: {', '.join(mismatch)}")

    v1_report = generate_report(session, v1.id)
    v2_report = generate_report(session, v2.id)
    ic_mean = _finite(v2_report["rank_ic"]["mean"])
    t_stat = _finite(v2_report["rank_ic"]["t_stat"])
    v2_total = _finite(v2_report["net_long_performance"]["total_return"])
    market_total = _finite(v2_report["benchmarks"]["equal_weight_market"]["total_return"])
    significant = bool(
        ic_mean is not None and t_stat is not None and ic_mean > 0 and abs(t_stat) >= 2
    )
    beats_market = bool(
        v2_total is not None and market_total is not None and v2_total > market_total
    )
    if significant and beats_market:
        verdict = "improved"
        headline = "样本外 IC 显著为正，且扣成本多头跑赢等权市场。"
    elif significant:
        verdict = "partial"
        headline = "样本外 IC 显著转正，但扣成本组合尚未跑赢等权市场。"
    else:
        verdict = "failed"
        headline = "样本外 IC 未显著为正，当前因子体系仍无可信 alpha 证据。"

    def summary(report: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": report["run"]["id"],
            "signal_id": report["run"]["signal_id"],
            "rank_ic": report["rank_ic"],
            "net_long": report["net_long_performance"],
            "long_short_gross": report["long_short_gross_diagnostic"],
            "benchmarks": report["benchmarks"],
            "costs": report["costs"],
        }

    v1_total = _finite(v1_report["net_long_performance"]["total_return"])
    return {
        "protocol": {
            "start_date": v1.start_date.isoformat(),
            "end_date": v1.end_date.isoformat(),
            "rebalance_freq": v1.rebalance_freq,
            "top_pct": v1.top_pct,
            "same_window_and_costs": True,
            "weights_frozen_before_test": True,
        },
        "v1": summary(v1_report),
        "v2": summary(v2_report),
        "delta": {
            "rank_ic_mean": (
                ic_mean - float(v1_report["rank_ic"]["mean"])
                if ic_mean is not None and _finite(v1_report["rank_ic"]["mean"]) is not None
                else None
            ),
            "net_total_return": (
                v2_total - v1_total if v2_total is not None and v1_total is not None else None
            ),
        },
        "curve": _comparison_curve(session, v1, v2),
        "verdict": {
            "status": verdict,
            "headline": headline,
            "significant_positive_ic": significant,
            "beats_equal_weight_market": beats_market,
            "policy": (
                "该裁定使用 S4 预注册三档门槛；无论结果如何，都不允许回看 test 窗修改 v2 权重。"
            ),
        },
        "limitations": [
            "test 窗仅 91 个交易日，结论是弱证据而非长期收益承诺。",
            "历史 PIT 缺口使 v2 实际只使用 4 个价量因子。",
            "多空序列未模拟融券可得性与借券成本，只作毛收益诊断。",
        ],
    }
