from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

MODEL_VERSION = "stock-score-v1.0.0"

DIMENSION_ORDER = ("tech", "capital", "fundamental", "valuation", "sentiment")
DIMENSION_LABELS: Mapping[str, str] = {
    "tech": "技术",
    "capital": "资金",
    "fundamental": "基本面",
    "valuation": "估值",
    "sentiment": "情绪",
}
DIMENSION_FACTORS: Mapping[str, tuple[str, ...]] = {
    "tech": ("momentum_20d", "momentum_60d"),
    "capital": ("net_inflow_5d",),
    "fundamental": ("roe", "net_profit_yoy"),
    "valuation": ("pe_percentile",),
    "sentiment": ("turnover_change_5d",),
}
DIMENSION_WEIGHTS: Mapping[str, float] = {
    "tech": 0.25,
    "capital": 0.20,
    "fundamental": 0.25,
    "valuation": 0.15,
    "sentiment": 0.15,
}
REQUIRED_FACTORS = tuple(
    factor for dimension in DIMENSION_ORDER for factor in DIMENSION_FACTORS[dimension]
)
OUTPUT_COLUMNS = (*DIMENSION_ORDER, "composite", "model_version")


def _numeric_factor(frame_z: pd.DataFrame, factor: str) -> pd.Series:
    if factor not in frame_z.columns:
        return pd.Series(np.nan, index=frame_z.index, dtype=float)
    values = pd.to_numeric(frame_z[factor], errors="coerce")
    return values.where(np.isfinite(values)).astype(float)


def _score(signal: pd.Series) -> pd.Series:
    return (5.0 + 2.0 * signal).clip(lower=0.0, upper=10.0)


def compute_stock_scores(frame_z: pd.DataFrame) -> pd.DataFrame:
    """Map a PIT factor z-score cross-section into fixed five-dimension scores.

    Missing and non-finite factor slots use neutral z=0 so every security keeps
    the same factor denominator and dimension weights. Output metadata records
    coverage so API consumers can disclose missing inputs rather than presenting
    an imputed 5.0 as observed evidence.
    """

    if frame_z.index.has_duplicates:
        raise ValueError("个股五维评分输入包含重复股票代码。")
    if not np.isclose(sum(DIMENSION_WEIGHTS.values()), 1.0):
        raise ValueError("个股五维评分权重之和必须为 1。")

    observed = pd.DataFrame(
        {factor: _numeric_factor(frame_z, factor) for factor in REQUIRED_FACTORS},
        index=frame_z.index,
    )
    available = observed.notna()
    neutral = observed.fillna(0.0)

    dimension_z = pd.DataFrame(index=frame_z.index, dtype=float)
    dimension_z["tech"] = (neutral["momentum_20d"] + neutral["momentum_60d"]) / 2.0
    dimension_z["capital"] = neutral["net_inflow_5d"]
    dimension_z["fundamental"] = (neutral["roe"] + neutral["net_profit_yoy"]) / 2.0
    dimension_z["valuation"] = -neutral["pe_percentile"]
    dimension_z["sentiment"] = neutral["turnover_change_5d"]

    result = pd.DataFrame(index=frame_z.index)
    for dimension in DIMENSION_ORDER:
        result[dimension] = _score(dimension_z[dimension])
    result["composite"] = sum(
        result[dimension] * DIMENSION_WEIGHTS[dimension] for dimension in DIMENSION_ORDER
    )
    complete = available.all(axis=1)
    result["model_version"] = MODEL_VERSION

    numeric = result[[*DIMENSION_ORDER, "composite"]].to_numpy(dtype=float)
    if not bool(np.isfinite(numeric).all()):
        raise ValueError("个股五维评分产生了非有限结果。")
    if not bool(((numeric >= 0.0) & (numeric <= 10.0)).all()):
        raise ValueError("个股五维评分超出 0-10 范围。")

    rows = len(frame_z)
    result.attrs = {
        "factor_coverage": {
            factor: {
                "count": int(available[factor].sum()),
                "ratio": round(float(available[factor].mean()), 6) if rows else 0.0,
            }
            for factor in REQUIRED_FACTORS
        },
        "dimension_complete": {
            dimension: int(available[list(DIMENSION_FACTORS[dimension])].all(axis=1).sum())
            for dimension in DIMENSION_ORDER
        },
        "full_rows": int(complete.sum()),
        "neutral_rows": int((~complete).sum()),
    }
    return result.loc[:, list(OUTPUT_COLUMNS)]
