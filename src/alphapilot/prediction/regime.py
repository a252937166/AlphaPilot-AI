from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from alphapilot.domain.models import MarketRegime, RegimeResult
from alphapilot.features.technical import compute_technical_features


class MarketRegimeClassifier:
    model_version = "rule-regime-v0.1.0"

    def classify(self, symbol: str, bars: pd.DataFrame) -> RegimeResult:
        features = compute_technical_features(bars)
        m20 = features["momentum_20d"]
        m60 = features["momentum_60d"]
        volatility = features["volatility_20d"]
        drawdown = features["drawdown_60d"]
        explanation: list[str] = []

        if volatility > 0.50 and abs(features["return_1d"]) > 0.035:
            regime = MarketRegime.EVENT_SHOCK
            confidence = min(0.9, 0.55 + volatility * 0.3)
            explanation.append("Short-term move and annualized volatility indicate an event shock.")
        elif m20 > 0.05 and m60 > 0.08 and drawdown > -0.06:
            regime = MarketRegime.TREND_UP
            confidence = 0.55 + min(0.3, (m20 + m60) * 0.8)
            explanation.append("20-day and 60-day momentum are both positive.")
        elif m20 < -0.05 and m60 < -0.08:
            regime = MarketRegime.TREND_DOWN
            confidence = 0.55 + min(0.3, abs(m20 + m60) * 0.8)
            explanation.append("20-day and 60-day momentum are both negative.")
        elif m20 > 0.02 and features["price_position_60d"] > 0.65:
            regime = MarketRegime.RISK_ON
            confidence = 0.58
            explanation.append(
                "Price is in the upper part of its 60-day range with positive momentum."
            )
        elif drawdown < -0.12 or volatility > 0.42:
            regime = MarketRegime.RISK_OFF
            confidence = 0.62
            explanation.append("Drawdown or volatility is elevated.")
        else:
            regime = MarketRegime.RANGE
            confidence = 0.52 + min(0.15, abs(m20) * 2)
            explanation.append("Momentum is insufficient for a stable directional regime.")

        as_of_value = pd.to_datetime(bars.sort_values("date").iloc[-1]["date"], utc=True)
        as_of = as_of_value.to_pydatetime() if not pd.isna(as_of_value) else datetime.now(UTC)
        return RegimeResult(
            symbol=symbol,
            regime=regime,
            confidence=float(np.clip(confidence, 0, 1)),
            as_of=as_of,
            features=features,
            explanation=explanation,
        )
