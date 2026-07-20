from __future__ import annotations

from datetime import timedelta

from alphapilot.domain.models import AlertAction, AlertUrgency, StockAlert, StockForecast


class AlertService:
    def evaluate(self, forecast: StockForecast) -> StockAlert:
        h5 = forecast.horizons["5d"]
        h20 = forecast.horizons["20d"]
        reasons: list[str]

        if h20.p_up >= 0.68 and h5.p_up >= 0.60 and h20.confidence >= 0.55:
            action = AlertAction.BUY_CANDIDATE
            urgency = AlertUrgency.MEDIUM
            change = 0.10
            reasons = [
                "Medium-horizon upward probability crossed the candidate threshold.",
                "Short and medium horizons are directionally aligned.",
            ]
            invalidation = (
                "Cancel when 20-day p_up falls below 0.55 or the investment thesis is invalidated."
            )
        elif h20.p_up <= 0.35 or h5.p_up <= 0.38:
            action = AlertAction.REDUCE
            urgency = AlertUrgency.HIGH
            change = -0.25
            reasons = [
                "Forecast distribution shifted materially to the downside.",
                "Risk review is required before maintaining or adding exposure.",
            ]
            invalidation = "Reassess when both 5-day and 20-day p_up recover above 0.50."
        elif h20.confidence < 0.35:
            action = AlertAction.REVIEW_REQUIRED
            urgency = AlertUrgency.MEDIUM
            change = 0.0
            reasons = ["Model confidence is too low for an automated directional action."]
            invalidation = "Resolve data quality issues or wait for a higher-confidence forecast."
        elif h20.p_up >= 0.56:
            action = AlertAction.WATCH
            urgency = AlertUrgency.LOW
            change = 0.0
            reasons = ["Forecast is constructive but below the buy-candidate threshold."]
            invalidation = "Remove from watch when 20-day p_up falls below 0.48."
        else:
            action = AlertAction.HOLD
            urgency = AlertUrgency.LOW
            change = 0.0
            reasons = ["No material threshold has been crossed."]
            invalidation = "Re-evaluate after a new event, material price move or model update."

        return StockAlert(
            symbol=forecast.symbol,
            action=action,
            urgency=urgency,
            confidence=h20.confidence,
            suggested_position_change=change,
            reasons=reasons,
            invalidation=invalidation,
            expires_at=forecast.as_of + timedelta(days=2),
            model_version=forecast.model_version,
            as_of=forecast.as_of,
        )
