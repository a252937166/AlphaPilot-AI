from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from math import floor, isfinite

MODEL_VERSION = "score-outcome-v1.0.0"
HORIZON = 20
DECILES = tuple(range(1, 11))


@dataclass(frozen=True, slots=True)
class OutcomeBucket:
    decile: int
    samples: int
    positive_samples: int
    win_rate: float | None


@dataclass(frozen=True, slots=True)
class OutcomeAggregate:
    buckets: tuple[OutcomeBucket, ...]
    input_rows: int
    evaluated_rows: int
    missing_endpoint_rows: int
    invalid_score_rows: int


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def score_decile(score: object) -> int:
    """Map a stored 0-100 percentile score to fixed, tie-stable deciles."""

    value = _finite_float(score)
    if value is None or not 0.0 <= value <= 100.0:
        raise ValueError("综合评分必须是 0-100 的有限数值。")
    if value == 100.0:
        return 10
    return floor(value / 10.0) + 1


def aggregate_outcomes(
    rows: list[tuple[object, object, object]],
) -> OutcomeAggregate:
    """Aggregate exact-origin/exact-horizon raw-close outcomes by score decile."""

    samples = {decile: 0 for decile in DECILES}
    positives = {decile: 0 for decile in DECILES}
    missing_endpoints = 0
    invalid_scores = 0
    evaluated = 0

    for raw_score, raw_origin_close, raw_horizon_close in rows:
        try:
            decile = score_decile(raw_score)
        except ValueError:
            invalid_scores += 1
            continue
        origin_close = _finite_float(raw_origin_close)
        horizon_close = _finite_float(raw_horizon_close)
        if origin_close is None or horizon_close is None or origin_close <= 0 or horizon_close <= 0:
            missing_endpoints += 1
            continue
        samples[decile] += 1
        if horizon_close / origin_close - 1.0 > 0.0:
            positives[decile] += 1
        evaluated += 1

    buckets = tuple(
        OutcomeBucket(
            decile=decile,
            samples=samples[decile],
            positive_samples=positives[decile],
            win_rate=(positives[decile] / samples[decile] if samples[decile] > 0 else None),
        )
        for decile in DECILES
    )
    return OutcomeAggregate(
        buckets=buckets,
        input_rows=len(rows),
        evaluated_rows=evaluated,
        missing_endpoint_rows=missing_endpoints,
        invalid_score_rows=invalid_scores,
    )


def nondecreasing_rates(buckets: tuple[OutcomeBucket, ...]) -> bool | None:
    """Report empirical monotonicity without altering or fabricating rates."""

    if tuple(bucket.decile for bucket in buckets) != DECILES:
        return None
    rates: list[float] = []
    for bucket in buckets:
        if bucket.win_rate is None:
            return None
        rates.append(bucket.win_rate)
    return all(left <= right for left, right in pairwise(rates))
