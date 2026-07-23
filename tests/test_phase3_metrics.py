from __future__ import annotations

import math
from itertools import pairwise

import pandas as pd
import pytest

from alphapilot.backtest.metrics import (
    calibration,
    ic_summary,
    layered_returns,
    long_short,
    performance,
    rank_ic,
    turnover_stats,
)


def test_rank_ic_is_one_for_perfect_order_and_minus_one_for_reverse() -> None:
    scores = pd.Series([1.0, 2.0, 3.0], index=["a", "b", "c"])

    assert rank_ic(scores, pd.Series([10.0, 20.0, 30.0], index=scores.index)) == (
        pytest.approx(1.0)
    )
    assert rank_ic(scores, pd.Series([30.0, 20.0, 10.0], index=scores.index)) == (
        pytest.approx(-1.0)
    )
    assert math.isnan(
        rank_ic(scores, pd.Series([1.0, 1.0, 1.0], index=scores.index))
    )


def test_ic_summary_uses_sample_std_and_excludes_missing() -> None:
    result = ic_summary([0.1, 0.2, 0.3, None, float("nan")])

    assert result["samples"] == 3
    assert result["mean"] == pytest.approx(0.2)
    assert result["std"] == pytest.approx(0.1)
    assert result["ic_ir"] == pytest.approx(2.0)
    assert result["t_stat"] == pytest.approx(0.2 / (0.1 / math.sqrt(3)))
    assert result["positive_ratio"] == pytest.approx(1.0)


def test_layered_returns_are_monotonic_and_long_short_uses_extremes() -> None:
    scores = pd.Series(
        range(1, 101),
        index=[f"s{index:03d}" for index in range(100)],
        dtype=float,
    )
    realized = scores / 1_000

    groups = layered_returns(scores, realized, n=10)

    assert len(groups) == 10
    assert all(left < right for left, right in pairwise(groups))
    assert long_short(groups) == pytest.approx(groups[-1] - groups[0])


def test_performance_calculates_drawdown_and_handles_flat_nav() -> None:
    result = performance(pd.Series([1.0, 1.1, 1.0, 1.2]))
    flat = performance(pd.Series([1.0, 1.0, 1.0]))

    assert result["observations"] == 4
    assert result["total_return"] == pytest.approx(0.2)
    assert result["max_drawdown"] == pytest.approx((1.0 / 1.1) - 1.0)
    assert result["ann_return"] is not None
    assert result["sharpe"] is not None
    assert result["calmar"] == pytest.approx(
        float(result["ann_return"]) / abs(float(result["max_drawdown"]))
    )
    assert flat["max_drawdown"] == 0.0
    assert flat["sharpe"] is None
    assert flat["calmar"] is None
    with pytest.raises(ValueError, match="positive"):
        performance(pd.Series([1.0, 0.0]))
    with pytest.raises(ValueError, match="missing"):
        performance(pd.Series([1.0, float("nan")]))


def test_turnover_stats_distinguish_trading_and_rebalance_days() -> None:
    result = turnover_stats(pd.DataFrame({"turnover": [None, 0.5, None, 1.0]}))

    assert result == pytest.approx(
        {
            "trading_days": 4,
            "rebalance_days": 2,
            "total": 1.5,
            "mean_rebalance": 0.75,
            "median_rebalance": 0.75,
            "max": 1.0,
            "annualized": 94.5,
        }
    )


def test_calibration_brier_and_reliability_curve() -> None:
    probabilities = pd.Series([0.0, 0.2, 0.8, 1.0], index=list("abcd"))
    realized = pd.Series([0, 0, 1, 1], index=list("abcd"))
    result = calibration(probabilities, realized, bins=5)
    perfect = calibration(
        pd.Series([0.0, 1.0]),
        pd.Series([0, 1]),
        bins=2,
    )

    assert result["samples"] == 4
    assert result["brier_score"] == pytest.approx(0.02)
    assert sum(item["count"] for item in result["curve"]) == 4
    assert result["curve"][0]["actual_rate"] == pytest.approx(0.0)
    assert result["curve"][-1]["actual_rate"] == pytest.approx(1.0)
    assert perfect["brier_score"] == 0.0
    with pytest.raises(ValueError, match="p_up"):
        calibration(pd.Series([1.1]), pd.Series([1]))
    with pytest.raises(ValueError, match="0/1"):
        calibration(pd.Series([0.5]), pd.Series([2]))
