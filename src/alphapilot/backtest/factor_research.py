from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from datetime import date
from time import monotonic
from typing import Any, Literal

import numpy as np
import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from alphapilot.backtest.factor_scope import HISTORICAL_FACTOR_CANDIDATES
from alphapilot.backtest.metrics import (
    ic_summary,
    layered_returns,
    long_short,
    rank_ic,
)
from alphapilot.backtest.pit import factor_zscores, forward_return
from alphapilot.data.provenance import AUDITED_DAILY_BAR_SOURCES
from alphapilot.db.models import DailyBar, FactorCorrelationStat, FactorICStat
from alphapilot.engines.factors import FACTOR_SET

_GROUP_COUNT = 10
_DECAY_HORIZONS = (5, 10, 20, 40)
_REBALANCE_DAYS = {"5d": 5, "10d": 10, "20d": 20}
_SAMPLE_TAGS = frozenset({"train", "test", "full"})
logger = logging.getLogger(__name__)


def _calendar(session: Session, start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("end must not be earlier than start")
    return list(
        session.scalars(
            select(DailyBar.trade_date)
            .where(
                DailyBar.trade_date >= start,
                DailyBar.trade_date <= end,
                DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
            )
            .distinct()
            .order_by(DailyBar.trade_date)
        )
    )


def _positive_int(value: int, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _interval(rebalance: str) -> int:
    normalized = str(rebalance).strip().lower()
    try:
        return _REBALANCE_DAYS[normalized]
    except KeyError as exc:
        raise ValueError("rebalance must be one of 5d/10d/20d") from exc


def _finite_or_none(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _mean_layers(periods: list[list[float]]) -> list[float | None]:
    if not periods:
        return [None] * _GROUP_COUNT
    frame = pd.DataFrame(periods, columns=range(_GROUP_COUNT), dtype=float)
    means = frame.mean(axis=0, skipna=True)
    return [_finite_or_none(value) for value in means.tolist()]


def _research(
    session: Session,
    factors: Iterable[str],
    start: date,
    end: date,
    *,
    horizon: int,
    rebalance: str,
) -> list[dict[str, Any]]:
    selected = list(dict.fromkeys(str(factor).strip() for factor in factors))
    unknown = sorted(set(selected).difference(FACTOR_SET))
    if not selected:
        raise ValueError("at least one factor is required")
    if unknown:
        raise ValueError(f"unknown factors: {unknown}")

    main_horizon = _positive_int(horizon, name="horizon")
    interval = _interval(rebalance)
    decay_horizons = sorted(set((*_DECAY_HORIZONS, main_horizon)))
    calendar = _calendar(session, start, end)
    started = monotonic()
    decision_count = sum(
        index + min(decay_horizons) < len(calendar)
        for index in range(0, len(calendar), interval)
    )
    decision_number = 0

    ic_values: dict[str, dict[int, list[float]]] = {
        factor: {period: [] for period in decay_horizons} for factor in selected
    }
    layers: dict[str, list[list[float]]] = {factor: [] for factor in selected}

    for decision_index in range(0, len(calendar), interval):
        available_horizons = [
            period for period in decay_horizons if decision_index + period < len(calendar)
        ]
        if not available_horizons:
            continue
        decision_number += 1
        decision_date = calendar[decision_index]
        scores = factor_zscores(session, decision_date).reindex(columns=selected)
        if scores.empty:
            continue

        for period in available_horizons:
            exit_date = calendar[decision_index + period]
            realized = forward_return(
                session,
                scores.index.astype(str).tolist(),
                decision_date,
                exit_date,
            )
            for factor in selected:
                factor_scores = pd.to_numeric(scores[factor], errors="coerce")
                value = rank_ic(factor_scores, realized)
                if math.isfinite(value):
                    ic_values[factor][period].append(value)
                if period == main_horizon:
                    period_layers = layered_returns(
                        factor_scores,
                        realized,
                        n=_GROUP_COUNT,
                    )
                    if any(math.isfinite(item) for item in period_layers):
                        layers[factor].append(period_layers)
        if decision_number == 1 or decision_number % 5 == 0:
            logger.info(
                "factor research progress decision=%d/%d date=%s factors=%d elapsed=%.1fs",
                decision_number,
                decision_count,
                decision_date,
                len(selected),
                monotonic() - started,
            )

    results: list[dict[str, Any]] = []
    for factor in selected:
        summary = ic_summary(ic_values[factor][main_horizon])
        mean_layers = _mean_layers(layers[factor])
        spread = long_short([float("nan") if value is None else value for value in mean_layers])
        decay: dict[str, dict[str, float | int | None]] = {}
        for period in decay_horizons:
            period_summary = ic_summary(ic_values[factor][period])
            decay[f"{period}d"] = {
                "ic_mean": period_summary["mean"],
                "n_periods": period_summary["samples"],
            }
        results.append(
            {
                "factor": factor,
                "ic_mean": summary["mean"],
                "ic_std": summary["std"],
                "ic_ir": summary["ic_ir"],
                "t_stat": summary["t_stat"],
                "ic_positive_ratio": summary["positive_ratio"],
                "n_periods": summary["samples"],
                "layered_returns": mean_layers,
                "long_short": _finite_or_none(spread),
                "decay": decay,
            }
        )
    return results


def single_factor_ic(
    session: Session,
    factor: str,
    start: date,
    end: date,
    horizon: int = 20,
    rebalance: str = "20d",
) -> dict[str, Any]:
    """Measure one strict-PIT factor against future adjusted close returns."""

    return _research(
        session,
        [factor],
        start,
        end,
        horizon=horizon,
        rebalance=rebalance,
    )[0]


def _persist_stats(
    session: Session,
    table: pd.DataFrame,
    *,
    sample_tag: Literal["train", "test", "full"],
    start: date,
    end: date,
) -> None:
    if sample_tag not in _SAMPLE_TAGS:
        raise ValueError("sample_tag must be one of train/test/full")
    for row in table.to_dict(orient="records"):
        factor = str(row["factor"])
        stored = session.scalar(
            select(FactorICStat).where(
                FactorICStat.factor == factor,
                FactorICStat.sample_tag == sample_tag,
                FactorICStat.start_date == start,
                FactorICStat.end_date == end,
            )
        )
        values = {
            "ic_mean": _finite_or_none(row["ic_mean"]),
            "ic_ir": _finite_or_none(row["ic_ir"]),
            "t_stat": _finite_or_none(row["t_stat"]),
            "ic_positive_ratio": _finite_or_none(row["ic_positive_ratio"]),
            "long_short": _finite_or_none(row["long_short"]),
            "n_periods": int(row["n_periods"]),
        }
        if stored is None:
            session.add(
                FactorICStat(
                    factor=factor,
                    sample_tag=sample_tag,
                    start_date=start,
                    end_date=end,
                    **values,
                )
            )
            continue
        for name, value in values.items():
            setattr(stored, name, value)
    session.flush()


def all_factors_ic(
    session: Session,
    start: date,
    end: date,
    *,
    sample_tag: Literal["train", "test", "full"] = "full",
) -> pd.DataFrame:
    """Research the audited historical factor scope and persist summaries.

    ``net_inflow_5d`` is deliberately absent: pre-snapshot membership cannot be
    reconstructed without look-ahead.  ``sector_strength`` remains live-only.
    """

    return factors_ic(
        session,
        HISTORICAL_FACTOR_CANDIDATES,
        start,
        end,
        sample_tag=sample_tag,
    )


def factors_ic(
    session: Session,
    factors: Iterable[str],
    start: date,
    end: date,
    *,
    sample_tag: Literal["train", "test", "full"],
) -> pd.DataFrame:
    """Research an explicit factor subset and persist one auditable sample."""

    table = research_factors_ic(session, factors, start, end)
    persist_factors_ic(
        session,
        table,
        sample_tag=sample_tag,
        start=start,
        end=end,
    )
    return table


def research_factors_ic(
    session: Session,
    factors: Iterable[str],
    start: date,
    end: date,
) -> pd.DataFrame:
    """Compute an explicit factor subset without opening a write transaction."""

    records = _research(
        session,
        factors,
        start,
        end,
        horizon=20,
        rebalance="20d",
    )
    return pd.DataFrame.from_records(records)


def persist_factors_ic(
    session: Session,
    table: pd.DataFrame,
    *,
    sample_tag: Literal["train", "test", "full"],
    start: date,
    end: date,
) -> None:
    """Persist a precomputed IC table in one short explicit transaction."""

    _persist_stats(
        session,
        table,
        sample_tag=sample_tag,
        start=start,
        end=end,
    )


def factor_correlation(
    session: Session,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Average cross-sectional factor correlations over fixed 20-day decisions."""

    calendar = _calendar(session, start, end)
    decision_dates = calendar[:: _REBALANCE_DAYS["20d"]]
    factor_count = len(HISTORICAL_FACTOR_CANDIDATES)
    correlation_sum = np.zeros((factor_count, factor_count), dtype=float)
    period_counts = np.zeros((factor_count, factor_count), dtype=int)

    for decision_date in decision_dates:
        frame = factor_zscores(session, decision_date).reindex(
            columns=HISTORICAL_FACTOR_CANDIDATES
        )
        if frame.empty:
            continue
        daily = frame.corr(method="pearson", min_periods=3).reindex(
            index=HISTORICAL_FACTOR_CANDIDATES,
            columns=HISTORICAL_FACTOR_CANDIDATES,
        )
        values = daily.to_numpy(dtype=float)
        finite = np.isfinite(values)
        correlation_sum[finite] += values[finite]
        period_counts[finite] += 1

    averages = np.full((factor_count, factor_count), np.nan, dtype=float)
    reliable = period_counts >= 3
    np.divide(
        correlation_sum,
        period_counts,
        out=averages,
        where=reliable,
    )
    result = pd.DataFrame(
        averages,
        index=HISTORICAL_FACTOR_CANDIDATES,
        columns=HISTORICAL_FACTOR_CANDIDATES,
        dtype=float,
    )
    count_table = pd.DataFrame(
        period_counts,
        index=HISTORICAL_FACTOR_CANDIDATES,
        columns=HISTORICAL_FACTOR_CANDIDATES,
        dtype=int,
    )
    redundant_pairs: list[dict[str, float | int | str]] = []
    for left_index, left in enumerate(HISTORICAL_FACTOR_CANDIDATES):
        for right in HISTORICAL_FACTOR_CANDIDATES[left_index + 1 :]:
            value = _finite_or_none(result.at[left, right])
            if value is not None and abs(value) > 0.8:
                redundant_pairs.append(
                    {
                        "left": left,
                        "right": right,
                        "correlation": value,
                        "n_periods": int(str(count_table.at[left, right])),
                    }
                )
    result.attrs = {
        "method": "mean_cross_sectional_pearson",
        "rebalance": "20d",
        "decision_dates": [value.isoformat() for value in decision_dates],
        "minimum_pair_periods": 3,
        "pair_periods": count_table.to_dict(),
        "redundant_pairs": redundant_pairs,
    }
    return result


def persist_factor_correlation(
    session: Session,
    corr: pd.DataFrame,
    *,
    sample_tag: Literal["train", "test", "full"],
    start: date,
    end: date,
) -> int:
    """Replace one correlation snapshot with its reliable upper-triangle cells."""

    if sample_tag not in _SAMPLE_TAGS:
        raise ValueError("sample_tag must be one of train/test/full")
    normalized = corr.reindex(
        index=HISTORICAL_FACTOR_CANDIDATES,
        columns=HISTORICAL_FACTOR_CANDIDATES,
    )
    pair_periods = corr.attrs.get("pair_periods", {})
    if not isinstance(pair_periods, dict):
        raise ValueError("corr is missing pair_periods audit metadata")

    session.execute(
        delete(FactorCorrelationStat).where(
            FactorCorrelationStat.sample_tag == sample_tag,
            FactorCorrelationStat.start_date == start,
            FactorCorrelationStat.end_date == end,
        )
    )
    stored = 0
    for left_index, left in enumerate(HISTORICAL_FACTOR_CANDIDATES):
        for right in HISTORICAL_FACTOR_CANDIDATES[left_index:]:
            value = _finite_or_none(normalized.at[left, right])
            column = pair_periods.get(right, {})
            raw_periods = column.get(left) if isinstance(column, dict) else None
            n_periods = (
                int(raw_periods)
                if isinstance(raw_periods, (int, float)) and math.isfinite(float(raw_periods))
                else 0
            )
            if value is None or n_periods < 3:
                continue
            session.add(
                FactorCorrelationStat(
                    left_factor=left,
                    right_factor=right,
                    sample_tag=sample_tag,
                    start_date=start,
                    end_date=end,
                    correlation=value,
                    n_periods=n_periods,
                )
            )
            stored += 1
    session.flush()
    return stored


def _redundancy_components(
    pairs: list[tuple[str, str]],
) -> list[set[str]]:
    components: list[set[str]] = []
    for left, right in pairs:
        matches = [component for component in components if left in component or right in component]
        if not matches:
            components.append({left, right})
            continue
        merged = {left, right}
        for component in matches:
            merged.update(component)
            components.remove(component)
        components.append(merged)
    return components


def classify_factors(ic_table: pd.DataFrame, corr: pd.DataFrame) -> dict[str, Any]:
    """Classify evidence and resolve reliable |corr| > 0.8 redundancy groups."""

    required = {
        "factor",
        "ic_mean",
        "ic_ir",
        "t_stat",
        "n_periods",
    }
    missing = sorted(required.difference(ic_table.columns))
    if missing:
        raise ValueError(f"ic_table missing columns: {missing}")
    rows = {str(row["factor"]): row for row in ic_table.to_dict(orient="records")}

    factors: dict[str, dict[str, Any]] = {}
    for factor in FACTOR_SET:
        if factor == "net_inflow_5d":
            factors[factor] = {
                "classification": "history_excluded_pit_gap",
                "direction": "unknown",
                "ic_mean": None,
                "ic_ir": None,
                "t_stat": None,
                "n_periods": 0,
                "direction_audit_required": False,
                "recommendation": (
                    "历史成分 PIT 不可重建；退出 S7/S9，待前向日快照形成新窗口。"
                ),
                "economic_note": "不使用当前成分伪造历史映射，不写偏差版 IC。",
                "redundant": False,
                "retained_factor": None,
            }
            continue
        row = rows.get(factor, {})
        ic_mean = _finite_or_none(row.get("ic_mean"))
        ic_ir = _finite_or_none(row.get("ic_ir"))
        t_stat = _finite_or_none(row.get("t_stat"))
        raw_periods = row.get("n_periods")
        n_periods = (
            int(raw_periods)
            if isinstance(raw_periods, (int, float)) and math.isfinite(raw_periods)
            else 0
        )
        if ic_mean is None or t_stat is None or n_periods < 2:
            classification = "insufficient_data"
            recommendation = "补齐历史 PIT 输入；当前权重置零，不翻转。"
        elif abs(t_stat) < 2:
            classification = "ineffective"
            recommendation = "弱证据；由 train IC_IR 自然降权，不人工挑选。"
        elif ic_mean > 0:
            classification = "significant_positive"
            recommendation = "保留正向定义，仍只允许 train IC_IR 定权。"
        else:
            classification = "significant_reverse"
            recommendation = "先完成源码方向审计；确认无 bug 后才允许负权。"
        direction = (
            "positive"
            if ic_mean is not None and ic_mean > 0
            else "negative"
            if ic_mean is not None and ic_mean < 0
            else "unknown"
        )
        factors[factor] = {
            "classification": classification,
            "direction": direction,
            "ic_mean": ic_mean,
            "ic_ir": ic_ir,
            "t_stat": t_stat,
            "n_periods": n_periods,
            "direction_audit_required": direction == "negative",
            "recommendation": recommendation,
            "economic_note": (
                "负向结果须在报告中记录公式、预期方向与 bug 判断。"
                if direction == "negative"
                else "无负向符号审计要求。"
            ),
            "redundant": False,
            "retained_factor": None,
        }

    normalized_corr = corr.reindex(index=FACTOR_SET, columns=FACTOR_SET)
    redundant_pairs: list[dict[str, float | str]] = []
    graph_edges: list[tuple[str, str]] = []
    for left_index, left in enumerate(FACTOR_SET):
        for right in FACTOR_SET[left_index + 1 :]:
            value = _finite_or_none(normalized_corr.at[left, right])
            if value is None or abs(value) <= 0.8:
                continue
            redundant_pairs.append(
                {
                    "left": left,
                    "right": right,
                    "correlation": value,
                }
            )
            graph_edges.append((left, right))

    redundancy_groups: list[dict[str, Any]] = []
    for component in _redundancy_components(graph_edges):
        ordered = sorted(component)
        candidates = [factor for factor in ordered if factors[factor]["ic_ir"] is not None]
        retained = (
            max(
                candidates,
                key=lambda factor: (
                    abs(float(factors[factor]["ic_ir"])),
                    -FACTOR_SET.index(factor),
                ),
            )
            if candidates
            else None
        )
        for factor in ordered:
            factors[factor]["retained_factor"] = retained
            factors[factor]["redundant"] = retained is not None and factor != retained
        redundancy_groups.append(
            {
                "factors": ordered,
                "retained_factor": retained,
                "rule": "max_abs_train_or_sample_ic_ir",
            }
        )

    return {
        "factors": factors,
        "redundant_pairs": redundant_pairs,
        "redundancy_groups": redundancy_groups,
        "threshold": 0.8,
    }
