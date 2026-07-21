from __future__ import annotations

from datetime import UTC, datetime

from alphapilot.core.config import Settings
from alphapilot.domain.models import PortfolioState, RiskDecision, TradeProposal, TradingMode


class TradeGuardrails:
    def __init__(self, settings: Settings):
        self.settings = settings

    def evaluate(
        self,
        proposal: TradeProposal,
        portfolio: PortfolioState,
        now: datetime | None = None,
    ) -> RiskDecision:
        current_time = now or datetime.now(UTC)
        if proposal.market_data_as_of.tzinfo is None:
            market_data_as_of = proposal.market_data_as_of.replace(tzinfo=UTC)
        else:
            market_data_as_of = proposal.market_data_as_of.astimezone(UTC)

        reasons: list[str] = []
        if proposal.mode in {TradingMode.RESEARCH, TradingMode.OBSERVE, TradingMode.ALERT}:
            reasons.append(f"交易模式 {proposal.mode} 不允许执行订单。")
        if (
            proposal.mode == TradingMode.LIMITED_LIVE_AUTO
            and not self.settings.live_trading_enabled
        ):
            reasons.append("实盘交易已被配置禁用。")
        if not self.settings.futu_enable_trade and proposal.mode in {
            TradingMode.PAPER_AUTO,
            TradingMode.LIMITED_LIVE_AUTO,
        }:
            reasons.append("富途交易网关已被配置禁用。")
        if proposal.confidence < self.settings.min_trade_confidence:
            reasons.append(
                f"置信度 {proposal.confidence:.2f} 低于最低要求 "
                f"{self.settings.min_trade_confidence:.2f}。"
            )

        age_seconds = (current_time - market_data_as_of).total_seconds()
        if age_seconds < -5:
            reasons.append("行情数据时间戳来自未来，数据异常。")
        elif age_seconds > self.settings.max_market_data_age_seconds:
            reasons.append(
                f"行情数据过期（{age_seconds:.0f}s > "
                f"{self.settings.max_market_data_age_seconds}s）。"
            )

        order_pct = proposal.estimated_notional / portfolio.equity
        post_trade_position = portfolio.current_position_pct + (
            order_pct if proposal.side.value == "BUY" else -order_pct
        )
        if post_trade_position > self.settings.max_single_position_pct:
            reasons.append(
                f"交易后单票仓位 {post_trade_position:.1%} 超过上限 "
                f"{self.settings.max_single_position_pct:.1%}。"
            )
        if proposal.side.value == "SELL" and post_trade_position < -1e-9:
            reasons.append("卖出数量超过当前多头持仓。")
        if (
            portfolio.sector_position_pct + max(order_pct, 0)
            > self.settings.max_sector_position_pct
        ):
            reasons.append("交易后行业仓位将超过配置上限。")
        if portfolio.daily_pnl_pct <= -self.settings.max_daily_loss_pct:
            reasons.append("当日亏损已触发 Kill Switch。")
        if portfolio.open_orders_for_symbol > 0:
            reasons.append("该标的已存在未完成订单。")
        if proposal.side.value == "BUY" and proposal.estimated_notional > portfolio.cash:
            reasons.append("可用资金不足以完成该笔买入。")

        requires_confirmation = proposal.mode in {
            TradingMode.CONFIRM_TO_TRADE,
            TradingMode.LIMITED_LIVE_AUTO,
        }
        return RiskDecision(
            approved=not reasons,
            reasons=reasons or ["全部配置的交易前检查已通过。"],
            evaluated_at=current_time,
            requires_human_confirmation=requires_confirmation,
        )
