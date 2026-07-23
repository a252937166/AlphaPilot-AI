from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date
from typing import Any, Literal

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.backtest.metrics import (
    ic_summary,
    layered_returns,
    long_short,
    rank_ic,
)
from alphapilot.backtest.pit import factor_zscores, forward_return
from alphapilot.data.provenance import AUDITED_DAILY_BAR_SOURCES
from alphapilot.db.models import DailyBar, FactorICStat
from alphapilot.engines.factors import FACTOR_SET

_GROUP_COUNT = 10
_DECAY_HORIZONS = (5, 10, 20, 40)
_REBALANCE_DAYS = {"5d": 5, "10d": 10, "20d": 20}
_SAMPLE_TAGS = frozenset({"train", "test", "full"})


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
    """Research the runtime factor set once per decision date and persist summaries."""

    records = _research(
        session,
        FACTOR_SET,
        start,
        end,
        horizon=20,
        rebalance="20d",
    )
    table = pd.DataFrame.from_records(records)
    _persist_stats(
        session,
        table,
        sample_tag=sample_tag,
        start=start,
        end=end,
    )
    return table
