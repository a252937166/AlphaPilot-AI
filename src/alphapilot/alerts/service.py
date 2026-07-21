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
                "20日上涨概率越过买入候选阈值。",
                "5日与20日预测方向一致。",
            ]
            invalidation = (
                "当20日上涨概率跌破0.55或投资逻辑失效时取消。"
            )
        elif h20.p_up <= 0.35 or h5.p_up <= 0.38:
            action = AlertAction.REDUCE
            urgency = AlertUrgency.HIGH
            change = -0.25
            reasons = [
                "预测分布显著转向下行。",
                "维持或增加仓位前需先复核风险。",
            ]
            invalidation = "当5日与20日上涨概率均回升至0.50以上时重新评估。"
        elif h20.confidence < 0.35:
            action = AlertAction.REVIEW_REQUIRED
            urgency = AlertUrgency.MEDIUM
            change = 0.0
            reasons = ["模型置信度过低，不适合自动给出方向性建议。"]
            invalidation = "先解决数据质量问题，或等待更高置信度的预测。"
        elif h20.p_up >= 0.56:
            action = AlertAction.WATCH
            urgency = AlertUrgency.LOW
            change = 0.0
            reasons = ["预测偏积极，但尚未达到买入候选阈值。"]
            invalidation = "当20日上涨概率跌破0.48时移出观察。"
        else:
            action = AlertAction.HOLD
            urgency = AlertUrgency.LOW
            change = 0.0
            reasons = ["未触发任何关键阈值。"]
            invalidation = "出现新事件、显著价格变动或模型更新后重新评估。"

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
