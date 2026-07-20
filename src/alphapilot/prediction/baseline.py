from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from alphapilot.domain.models import HorizonForecast, StockForecast
from alphapilot.features.technical import compute_technical_features


class BaselineForecastEngine:
    """Transparent engineering baseline; not a production alpha model."""

    model_version = "transparent-baseline-v0.1.0"

    @staticmethod
    def _sigmoid(value: float) -> float:
        clipped = float(np.clip(value, -20, 20))
        return 1 / (1 + math.exp(-clipped))

    def _forecast_horizon(self, features: dict[str, float], horizon: int) -> HorizonForecast:
        momentum_weight = min(1.0, math.sqrt(horizon / 20))
        raw_score = (
            4.0 * features["momentum_20d"] * momentum_weight
            + 2.0 * features["momentum_5d"]
            + 2.5 * features["ma_gap_5_20"]
            + 1.5 * features["ma_gap_20_60"]
            + 0.35 * (features["volume_ratio_5_20"] - 1)
            + 0.8 * (features["price_position_60d"] - 0.5)
            - 0.55 * features["volatility_20d"]
            + 0.5 * features["drawdown_60d"]
        )
        p_up = float(np.clip(self._sigmoid(raw_score), 0.05, 0.95))

        expected = (
            0.32 * features["momentum_20d"] * math.sqrt(horizon / 20)
            + 0.10 * features["momentum_5d"]
            + 0.12 * features["ma_gap_5_20"]
            - 0.03 * features["volatility_20d"] * math.sqrt(horizon / 252)
        )
        expected = float(np.clip(expected, -0.35, 0.35))
        sigma = max(0.005, features["volatility_20d"] * math.sqrt(horizon / 252))
        confidence = float(
            np.clip(
                0.35
                + 0.65 * abs(p_up - 0.5)
                + 0.20 * features["data_completeness"]
                - 0.15 * min(features["volatility_20d"], 1.0),
                0.2,
                0.82,
            )
        )

        return HorizonForecast(
            horizon_days=horizon,
            p_up=p_up,
            expected_return=expected,
            q10=expected - 1.2816 * sigma,
            q50=expected,
            q90=expected + 1.2816 * sigma,
            confidence=confidence,
        )

    def forecast(self, symbol: str, bars: pd.DataFrame, provider: str) -> StockForecast:
        features = compute_technical_features(bars)
        warnings = [
            "This is a transparent baseline for pipeline validation, not a production alpha model."
        ]
        if len(bars) < 120:
            warnings.append(
                "History is shorter than 120 observations; medium-horizon reliability is reduced."
            )
        as_of_value = pd.to_datetime(bars.sort_values("date").iloc[-1]["date"], utc=True)
        as_of = as_of_value.to_pydatetime() if not pd.isna(as_of_value) else datetime.now(UTC)
        return StockForecast(
            symbol=symbol,
            as_of=as_of,
            provider=provider,
            model_version=self.model_version,
            data_points=len(bars),
            features=features,
            horizons={
                "1d": self._forecast_horizon(features, 1),
                "5d": self._forecast_horizon(features, 5),
                "20d": self._forecast_horizon(features, 20),
            },
            warnings=warnings,
        )
