from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

_ANNUAL_TRADING_DAYS = 252


def _numeric_series(values: pd.Series | Sequence[float | None]) -> pd.Series:
    series = (
        values
        if isinstance(values, pd.Series)
        else pd.Series(
            [float(value) if value is not None else float("nan") for value in values],
            dtype=float,
        )
    )
    numeric = pd.to_numeric(series, errors="coerce").astype(float)
    return numeric.loc[numeric.map(math.isfinite)]


def _paired(
    left: pd.Series,
    right: pd.Series,
    *,
    left_name: str,
    right_name: str,
) -> pd.DataFrame:
    left_values = pd.to_numeric(left, errors="coerce").rename(left_name)
    right_values = pd.to_numeric(right, errors="coerce").rename(right_name)
    paired = pd.concat([left_values, right_values], axis=1, join="inner").dropna()
    if paired.empty:
        return paired
    finite = paired[left_name].map(math.isfinite) & paired[right_name].map(
        math.isfinite
    )
    return paired.loc[finite]


def rank_ic(scores: pd.Series, fwd_returns: pd.Series) -> float:
    """Return Spearman rank IC over finite, index-aligned observations."""

    paired = _paired(
        scores,
        fwd_returns,
        left_name="score",
        right_name="return",
    )
    if len(paired) < 2:
        return float("nan")
    score_ranks = paired["score"].rank(method="average")
    return_ranks = paired["return"].rank(method="average")
    if score_ranks.nunique() < 2 or return_ranks.nunique() < 2:
        return float("nan")
    value = score_ranks.corr(return_ranks)
    return float(value) if pd.notna(value) else float("nan")


def ic_summary(
    ic_series: pd.Series | Sequence[float | None],
) -> dict[str, float | int | None]:
    """Summarize an IC series without silently treating missing days as zero."""

    values = _numeric_series(ic_series)
    samples = len(values)
    if samples == 0:
        return {
            "samples": 0,
            "mean": None,
            "std": None,
            "ic_ir": None,
            "t_stat": None,
            "positive_ratio": None,
        }
    mean = float(values.mean())
    std = float(values.std(ddof=1)) if samples > 1 else None
    ic_ir = mean / std if std is not None and std > 0 else None
    t_stat = (
        mean / (std / math.sqrt(samples))
        if std is not None and std > 0
        else None
    )
    return {
        "samples": samples,
        "mean": mean,
        "std": std,
        "ic_ir": ic_ir,
        "t_stat": t_stat,
        "positive_ratio": float((values > 0).mean()),
    }


def layered_returns(
    scores: pd.Series,
    fwd_returns: pd.Series,
    n: int = 10,
) -> list[float]:
    """Return low-to-high score group mean returns with deterministic ties."""

    if isinstance(n, bool) or not isinstance(n, int) or n < 2:
        raise ValueError("n must be an integer greater than one")
    paired = _paired(
        scores,
        fwd_returns,
        left_name="score",
        right_name="return",
    )
    if paired.empty:
        return [float("nan")] * n
    ordered = (
        paired.assign(_symbol=paired.index.astype(str))
        .sort_values(["score", "_symbol"], kind="stable")
        .drop(columns="_symbol")
    )
    if len(ordered) == 1:
        singleton_result = [float("nan")] * n
        singleton_result[-1] = float(ordered.iloc[0]["return"])
        return singleton_result
    group_ids = [
        min(n - 1, index * n // (len(ordered) - 1))
        for index in range(len(ordered))
    ]
    result: list[float] = []
    for group_id in range(n):
        group = ordered.iloc[
            [index for index, value in enumerate(group_ids) if value == group_id]
        ]["return"]
        result.append(float(group.mean()) if not group.empty else float("nan"))
    return result


def long_short(layered: Sequence[float]) -> float:
    """Return highest-score group minus lowest-score group."""

    if len(layered) < 2:
        return float("nan")
    low = float(layered[0])
    high = float(layered[-1])
    if not math.isfinite(low) or not math.isfinite(high):
        return float("nan")
    return high - low


def performance(nav: pd.Series) -> dict[str, float | int | None]:
    """Calculate annualized return, Sharpe, drawdown, and Calmar from NAV."""

    values = pd.to_numeric(nav, errors="coerce").astype(float)
    if values.empty:
        return {
            "observations": 0,
            "total_return": None,
            "ann_return": None,
            "ann_volatility": None,
            "sharpe": None,
            "max_drawdown": None,
            "calmar": None,
        }
    if values.isna().any() or not values.map(math.isfinite).all():
        raise ValueError("nav must not contain missing or non-finite values")
    if (values <= 0).any():
        raise ValueError("nav must contain only positive values")
    observations = len(values)
    total_return = float(values.iloc[-1] / values.iloc[0] - 1.0)
    periods = observations - 1
    ann_return = (
        float((values.iloc[-1] / values.iloc[0]) ** (_ANNUAL_TRADING_DAYS / periods) - 1.0)
        if periods > 0
        else None
    )
    returns = values.pct_change(fill_method=None).dropna()
    daily_std = float(returns.std(ddof=1)) if len(returns) > 1 else None
    ann_volatility = (
        daily_std * math.sqrt(_ANNUAL_TRADING_DAYS)
        if daily_std is not None and daily_std > 0
        else None
    )
    sharpe = (
        float(returns.mean()) / daily_std * math.sqrt(_ANNUAL_TRADING_DAYS)
        if daily_std is not None and daily_std > 0
        else None
    )
    running_max = pd.Series(
        np.maximum.accumulate(values.to_numpy(dtype=float)),
        index=values.index,
        dtype=float,
    )
    drawdown = values / running_max - 1.0
    max_drawdown = float(drawdown.min())
    calmar = (
        ann_return / abs(max_drawdown)
        if ann_return is not None and max_drawdown < 0
        else None
    )
    return {
        "observations": observations,
        "total_return": total_return,
        "ann_return": ann_return,
        "ann_volatility": ann_volatility,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "calmar": calmar,
    }


def turnover_stats(
    daily: pd.DataFrame | pd.Series | Sequence[float | None],
) -> dict[str, float | int | None]:
    """Summarize rebalance turnover while keeping non-rebalance days explicit."""

    if isinstance(daily, pd.DataFrame):
        if "turnover" not in daily.columns:
            raise ValueError("daily frame must contain turnover")
        raw = daily["turnover"]
        trading_days = len(daily)
    elif isinstance(daily, pd.Series):
        raw = daily
        trading_days = len(daily)
    else:
        raw = pd.Series(daily, dtype=float)
        trading_days = len(daily)
    values = _numeric_series(raw)
    if (values < 0).any():
        raise ValueError("turnover must not be negative")
    if values.empty:
        return {
            "trading_days": trading_days,
            "rebalance_days": 0,
            "total": 0.0,
            "mean_rebalance": None,
            "median_rebalance": None,
            "max": None,
            "annualized": 0.0,
        }
    return {
        "trading_days": trading_days,
        "rebalance_days": len(values),
        "total": float(values.sum()),
        "mean_rebalance": float(values.mean()),
        "median_rebalance": float(values.median()),
        "max": float(values.max()),
        "annualized": (
            float(values.sum()) / trading_days * _ANNUAL_TRADING_DAYS
            if trading_days > 0
            else None
        ),
    }


def calibration(
    p_up: pd.Series,
    realized_up: pd.Series,
    bins: int = 10,
) -> dict[str, Any]:
    """Return Brier score and a fixed-bin probability reliability curve."""

    if isinstance(bins, bool) or not isinstance(bins, int) or bins < 2:
        raise ValueError("bins must be an integer greater than one")
    paired = _paired(
        p_up,
        realized_up,
        left_name="p_up",
        right_name="realized_up",
    )
    if ((paired["p_up"] < 0) | (paired["p_up"] > 1)).any():
        raise ValueError("p_up must be within [0, 1]")
    if not paired["realized_up"].isin([0.0, 1.0]).all():
        raise ValueError("realized_up must contain only 0/1 values")
    if paired.empty:
        return {
            "samples": 0,
            "brier_score": None,
            "curve": [
                {
                    "bin": index + 1,
                    "lower": index / bins,
                    "upper": (index + 1) / bins,
                    "count": 0,
                    "predicted_mean": None,
                    "actual_rate": None,
                }
                for index in range(bins)
            ],
        }

    brier = float(
        np.mean(
            np.square(
                paired["p_up"].to_numpy(dtype=float)
                - paired["realized_up"].to_numpy(dtype=float)
            )
        )
    )
    bin_ids = np.minimum(
        (paired["p_up"].to_numpy(dtype=float) * bins).astype(int),
        bins - 1,
    )
    curve: list[dict[str, float | int | None]] = []
    for index in range(bins):
        group = paired.iloc[np.flatnonzero(bin_ids == index)]
        curve.append(
            {
                "bin": index + 1,
                "lower": index / bins,
                "upper": (index + 1) / bins,
                "count": len(group),
                "predicted_mean": (
                    float(group["p_up"].mean()) if not group.empty else None
                ),
                "actual_rate": (
                    float(group["realized_up"].mean()) if not group.empty else None
                ),
            }
        )
    return {
        "samples": len(paired),
        "brier_score": brier,
        "curve": curve,
    }
