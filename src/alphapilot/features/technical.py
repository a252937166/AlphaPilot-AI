from __future__ import annotations

import math

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = {"date", "open", "high", "low", "close", "volume"}


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def compute_technical_features(bars: pd.DataFrame) -> dict[str, float]:
    missing = REQUIRED_COLUMNS.difference(bars.columns)
    if missing:
        raise ValueError(f"Bars are missing columns: {sorted(missing)}")
    if len(bars) < 30:
        raise ValueError("At least 30 daily bars are required")

    frame = bars.copy().sort_values("date").reset_index(drop=True)
    close = pd.to_numeric(frame["close"], errors="coerce")
    volume = pd.to_numeric(frame["volume"], errors="coerce").replace(0, np.nan)
    returns = close.pct_change()

    latest = close.iloc[-1]
    peak_60 = close.tail(60).max()
    low_60 = close.tail(60).min()
    range_60 = peak_60 - low_60

    features = {
        "return_1d": _safe_float(close.pct_change(1).iloc[-1]),
        "momentum_5d": _safe_float(close.pct_change(5).iloc[-1]),
        "momentum_20d": _safe_float(close.pct_change(20).iloc[-1]),
        "momentum_60d": _safe_float(close.pct_change(60).iloc[-1]),
        "ma_gap_5_20": _safe_float(close.tail(5).mean() / close.tail(20).mean() - 1),
        "ma_gap_20_60": _safe_float(
            close.tail(20).mean() / close.tail(min(60, len(close))).mean() - 1
        ),
        "volatility_20d": _safe_float(returns.tail(20).std(ddof=0) * np.sqrt(252)),
        "drawdown_60d": _safe_float(latest / peak_60 - 1),
        "price_position_60d": _safe_float((latest - low_60) / range_60 if range_60 else 0.5),
        "volume_ratio_5_20": _safe_float(volume.tail(5).mean() / volume.tail(20).mean(), 1.0),
        "data_completeness": _safe_float(1 - frame.isna().mean().mean(), 0.0),
    }
    return features
