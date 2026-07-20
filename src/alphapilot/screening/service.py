from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np

from alphapilot.data.base import MarketDataProvider
from alphapilot.domain.models import (
    ScreeningCandidate,
    ScreeningRequest,
    ScreeningResponse,
)
from alphapilot.prediction.baseline import BaselineForecastEngine


class ScreeningService:
    def __init__(self, provider: MarketDataProvider, engine: BaselineForecastEngine):
        self.provider = provider
        self.engine = engine

    def run(self, request: ScreeningRequest) -> ScreeningResponse:
        end = date.today()
        start = end - timedelta(days=int(request.lookback_days * 1.7))
        raw_candidates: list[ScreeningCandidate] = []
        failed: dict[str, str] = {}

        for symbol in request.symbols:
            try:
                bars = self.provider.get_daily_bars(symbol, start, end)
                forecast = self.engine.forecast(symbol, bars, self.provider.name)
                h5 = forecast.horizons["5d"]
                h20 = forecast.horizons["20d"]
                volatility = forecast.features["volatility_20d"]

                trend_score = float(np.clip(50 + (h20.p_up - 0.5) * 150, 0, 100))
                risk_score = float(np.clip(100 - volatility * 110, 0, 100))
                quality_placeholder = 50.0
                score = 0.60 * trend_score + 0.25 * risk_score + 0.15 * quality_placeholder

                reasons = [
                    f"20-day upward probability is {h20.p_up:.1%}.",
                    f"20-day expected return is {h20.expected_return:.2%}.",
                    f"20-day annualized realized volatility is {volatility:.1%}.",
                ]
                raw_candidates.append(
                    ScreeningCandidate(
                        rank=0,
                        symbol=symbol,
                        score=float(np.clip(score, 0, 100)),
                        trend_score=trend_score,
                        risk_score=risk_score,
                        quality_placeholder_score=quality_placeholder,
                        p_up_5d=h5.p_up,
                        p_up_20d=h20.p_up,
                        expected_return_20d=h20.expected_return,
                        confidence_20d=h20.confidence,
                        reasons=reasons,
                        warnings=forecast.warnings,
                    )
                )
            except Exception as exc:  # one symbol must not abort a market-wide screen
                failed[symbol] = str(exc)

        ordered = sorted(raw_candidates, key=lambda item: item.score, reverse=True)[: request.top_n]
        candidates = [
            candidate.model_copy(update={"rank": rank}) for rank, candidate in enumerate(ordered, 1)
        ]
        return ScreeningResponse(
            generated_at=datetime.now(UTC),
            provider=self.provider.name,
            model_version=self.engine.model_version,
            requested=len(request.symbols),
            succeeded=len(raw_candidates),
            failed=failed,
            candidates=candidates,
        )
