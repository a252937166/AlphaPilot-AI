from __future__ import annotations

import math
from itertools import pairwise
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.backtest.metrics import (
    ic_summary,
    performance,
    rank_ic,
    turnover_stats,
)
from alphapilot.core.timeutil import iso_utc
from alphapilot.db.models import BacktestDaily, BacktestRun, utcnow

_SIGNIFICANCE_T_THRESHOLD = 1.96
_LAYER_COUNT = 10


def _daily_frame(rows: list[BacktestDaily]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": [row.trade_date for row in rows],
            "rank_ic": [row.rank_ic for row in rows],
            "long_ret": [row.long_ret for row in rows],
            "ls_ret": [row.ls_ret for row in rows],
            "turnover": [row.turnover for row in rows],
            "nav": [row.nav for row in rows],
            "benchmark_nav": [row.benchmark_nav for row in rows],
            "market_nav": [row.market_nav for row in rows],
        }
    )


def _layer_summary(rows: list[BacktestDaily]) -> dict[str, Any]:
    means: list[float | None] = []
    observations: list[int] = []
    for group_index in range(_LAYER_COUNT):
        values = [
            float(row.group_returns[group_index])
            for row in rows
            if len(row.group_returns) == _LAYER_COUNT
            and row.group_returns[group_index] is not None
            and math.isfinite(float(row.group_returns[group_index]))
        ]
        observations.append(len(values))
        means.append(float(np.mean(values)) if values else None)
    valid = [
        (index + 1, value)
        for index, value in enumerate(means)
        if value is not None
    ]
    monotonic_rank_ic: float | None = None
    if len(valid) >= 2:
        group_numbers = pd.Series(
            [float(item[0]) for item in valid],
            index=[str(item[0]) for item in valid],
        )
        group_returns = pd.Series(
            [float(item[1]) for item in valid],
            index=[str(item[0]) for item in valid],
        )
        value = rank_ic(group_numbers, group_returns)
        monotonic_rank_ic = value if math.isfinite(value) else None
    top_minus_bottom = (
        float(means[-1] - means[0])
        if means[0] is not None and means[-1] is not None
        else None
    )
    strictly_monotonic = (
        all(
            left is not None
            and right is not None
            and left < right
            for left, right in pairwise(means)
        )
        if all(value is not None for value in means)
        else False
    )
    return {
        "labels": [f"G{index}" for index in range(1, _LAYER_COUNT + 1)],
        "mean_daily_returns": means,
        "observations": observations,
        "top_minus_bottom": top_minus_bottom,
        "monotonic_rank_ic": monotonic_rank_ic,
        "strictly_monotonic": strictly_monotonic,
    }


def _gross_long_short(frame: pd.DataFrame) -> dict[str, Any]:
    returns = pd.to_numeric(frame["ls_ret"], errors="coerce").dropna().astype(float)
    returns = returns.loc[returns.map(math.isfinite)]
    if returns.empty or (returns <= -1).any():
        return {
            "available": False,
            "reason": "没有足够的有限分层多空收益。",
            "costed": False,
            "tradable": False,
        }
    nav = pd.Series(
        np.concatenate(
            (
                np.array([1.0]),
                np.cumprod(1.0 + returns.to_numpy(dtype=float)),
            )
        ),
        dtype=float,
    )
    return {
        "available": True,
        "costed": False,
        "tradable": False,
        "label": "G10-G1 毛收益研究诊断",
        "metrics": performance(nav),
        "warning": (
            "A股裸空并非默认可交易能力；该序列未模拟融券可得性、借券成本或双边调仓成本，"
            "不得作为可交易净多空收益。"
        ),
    }


def _effective_window(
    rows: list[BacktestDaily],
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, int, int | None]:
    first_trade_index = next(
        (
            index
            for index, row in enumerate(rows)
            if row.turnover is not None
            and math.isfinite(float(row.turnover))
            and float(row.turnover) > 0
        ),
        None,
    )
    baseline_index = (
        max(first_trade_index - 1, 0)
        if first_trade_index is not None
        else 0
    )
    return (
        frame.iloc[baseline_index:].reset_index(drop=True),
        baseline_index,
        first_trade_index,
    )


def _limitations(
    run: BacktestRun,
    effective_rows: list[BacktestDaily],
) -> list[dict[str, str]]:
    interval_days = (
        effective_rows[-1].trade_date - effective_rows[0].trade_date
    ).days
    limitations = [
        {
            "code": "survivorship_bias",
            "severity": "high",
            "text": (
                "证券主表只含当前存续 A 股，缺少历史退市股，结果带幸存者偏差，"
                "不能宣称无偏。"
            ),
        },
        {
            "code": "financial_available_time",
            "severity": "high",
            "text": (
                "财务数据虽按 available_time 截断，但部分历史披露时间来自供应商近似，"
                "仍可能存在时间戳误差。"
            ),
        },
        {
            "code": "historical_st_and_ipo_rules",
            "severity": "medium",
            "text": (
                "缺少历史 ST 状态和新股无涨跌幅限制事件；未知日期按 ±5% 保守限幅，"
                "会少算部分真实可成交机会。"
            ),
        },
        {
            "code": "short_history",
            "severity": "high",
            "text": (
                f"有效绩效样本仅 {len(effective_rows)} 个交易日 / "
                f"{interval_days} 个自然日，"
                "不足以覆盖多个完整牛熊周期。"
            ),
        },
        {
            "code": "statistical_inference",
            "severity": "medium",
            "text": (
                "IC t-stat 使用普通样本标准误，未对日频自相关、异方差或多重检验做 "
                "HAC/Newey-West 修正，只能作为基线诊断。"
            ),
        },
        {
            "code": "long_short_not_tradable",
            "severity": "high",
            "text": (
                "G10-G1 仅为毛收益研究诊断，未模拟融券可得性和借券成本；"
                "可交易结论只使用已逐笔扣成本的多头组合。"
            ),
        },
        {
            "code": "probability_calibration_unavailable",
            "severity": "medium",
            "text": (
                "composite-v1 是排序分而非上涨概率，不能计算概率校准；"
                "没有将分数除以 100 冒充 p_up。"
            ),
        },
    ]
    if int(run.summary.get("missing_benchmark_days", 0) or 0) > 0:
        limitations.append(
            {
                "code": "benchmark_gaps",
                "severity": "high",
                "text": "沪深300基准存在缺失交易日，相关超额结论不完整。",
            }
        )
    return limitations


def _honest_conclusion(
    strategy: dict[str, float | int | None],
    csi300: dict[str, float | int | None],
    market: dict[str, float | int | None],
    ic: dict[str, float | int | None],
    layers: dict[str, Any],
) -> dict[str, Any]:
    ic_mean = ic.get("mean")
    t_stat = ic.get("t_stat")
    strategy_total = strategy.get("total_return")
    csi_total = csi300.get("total_return")
    market_total = market.get("total_return")
    gates = {
        "positive_significant_ic": bool(
            isinstance(ic_mean, float)
            and isinstance(t_stat, float)
            and ic_mean > 0
            and t_stat >= _SIGNIFICANCE_T_THRESHOLD
        ),
        "positive_net_return": bool(
            isinstance(strategy_total, float) and strategy_total > 0
        ),
        "beats_csi300": bool(
            isinstance(strategy_total, float)
            and isinstance(csi_total, float)
            and strategy_total > csi_total
        ),
        "beats_equal_weight_market": bool(
            isinstance(strategy_total, float)
            and isinstance(market_total, float)
            and strategy_total > market_total
        ),
        "top_layer_beats_bottom": bool(
            isinstance(layers.get("top_minus_bottom"), float)
            and layers["top_minus_bottom"] > 0
        ),
    }
    supported = all(gates.values())
    if supported:
        status = "alpha_supported_in_sample"
        headline = "当前基线因子在该样本内显示统计显著、扣成本后仍为正的 alpha。"
    else:
        status = "no_reliable_alpha_evidence"
        failed = [name for name, passed in gates.items() if not passed]
        headline = (
            "当前基线因子在该样本内没有形成可信、扣成本后仍成立的 alpha 证据。"
        )
        return {
            "status": status,
            "alpha_supported": False,
            "headline": headline,
            "gates": gates,
            "failed_gates": failed,
            "policy": (
                "不因结论为负而调参重跑；任何优化必须在后续预先定义、时间隔离的样本外流程验证。"
            ),
        }
    return {
        "status": status,
        "alpha_supported": True,
        "headline": headline,
        "gates": gates,
        "failed_gates": [],
        "policy": "该结论仍受报告所列数据局限约束，不能直接外推为实盘收益承诺。",
    }


def generate_report(session: Session, run_id: int) -> dict[str, Any]:
    """Generate a JSON-safe, evidence-grounded report for one completed run."""

    run = session.get(BacktestRun, run_id)
    if run is None:
        raise ValueError(f"backtest run not found: {run_id}")
    if run.status != "completed":
        raise ValueError(f"backtest run is not completed: {run.status}")
    rows = list(
        session.scalars(
            select(BacktestDaily)
            .where(BacktestDaily.run_id == run_id)
            .order_by(BacktestDaily.trade_date)
        )
    )
    if not rows:
        raise ValueError(f"backtest run has no daily rows: {run_id}")

    frame = _daily_frame(rows)
    effective_frame, baseline_index, first_trade_index = _effective_window(
        rows,
        frame,
    )
    effective_rows = rows[baseline_index:]
    strategy = performance(effective_frame["nav"])
    csi300 = performance(effective_frame["benchmark_nav"])
    market = performance(effective_frame["market_nav"])
    ic = ic_summary(frame["rank_ic"])
    layers = _layer_summary(rows)
    turnover = turnover_stats(effective_frame)
    total_cost = float(run.summary.get("total_cost", 0.0) or 0.0)
    total_traded = float(run.summary.get("total_traded", 0.0) or 0.0)
    initial_capital = float(run.params.get("initial_capital", 0.0) or 0.0)
    strategy_total = strategy.get("total_return")
    csi_total = csi300.get("total_return")
    market_total = market.get("total_return")
    raw_ic_samples = ic.get("samples")
    ic_samples = (
        int(raw_ic_samples)
        if isinstance(raw_ic_samples, (int, float))
        else 0
    )

    return {
        "run": {
            "id": run.id,
            "name": run.name,
            "signal_id": run.signal_id,
            "status": run.status,
            "start_date": run.start_date.isoformat(),
            "end_date": run.end_date.isoformat(),
            "rebalance_freq": run.rebalance_freq,
            "top_pct": run.top_pct,
            "params": run.params,
            "created_at": iso_utc(run.created_at),
        },
        "generated_at": iso_utc(utcnow()),
        "coverage": {
            "trading_days": len(rows),
            "requested_first_trade_date": rows[0].trade_date.isoformat(),
            "requested_last_trade_date": rows[-1].trade_date.isoformat(),
            "first_execution_date": (
                rows[first_trade_index].trade_date.isoformat()
                if first_trade_index is not None
                else None
            ),
            "effective_start_date": effective_rows[0].trade_date.isoformat(),
            "effective_trading_days": len(effective_rows),
            "warmup_days_excluded_from_performance": baseline_index,
            "rank_ic_days": ic_samples,
            "rank_ic_unavailable_days": len(rows) - ic_samples,
            "day_errors": list(run.summary.get("day_errors", [])),
            "missing_benchmark_days": int(
                run.summary.get("missing_benchmark_days", 0) or 0
            ),
        },
        "rank_ic": ic,
        "layers": layers,
        "net_long_performance": strategy,
        "long_short_gross_diagnostic": _gross_long_short(effective_frame),
        "benchmarks": {
            "csi300": csi300,
            "equal_weight_market": market,
            "excess_total_return": {
                "vs_csi300": (
                    float(strategy_total - csi_total)
                    if isinstance(strategy_total, float)
                    and isinstance(csi_total, float)
                    else None
                ),
                "vs_equal_weight_market": (
                    float(strategy_total - market_total)
                    if isinstance(strategy_total, float)
                    and isinstance(market_total, float)
                    else None
                ),
            },
        },
        "turnover": turnover,
        "costs": {
            "total": total_cost,
            "initial_capital": initial_capital,
            "to_initial_capital": (
                total_cost / initial_capital if initial_capital > 0 else None
            ),
            "total_traded": total_traded,
            "bps_of_traded_notional": (
                total_cost / total_traded * 10_000 if total_traded > 0 else None
            ),
        },
        "probability_calibration": {
            "available": False,
            "reason": (
                "composite-v1 输出排序分而非 p_up 概率；不把 0–100 分数伪装为概率。"
            ),
        },
        "conclusion": _honest_conclusion(strategy, csi300, market, ic, layers),
        "limitations": _limitations(run, effective_rows),
    }
