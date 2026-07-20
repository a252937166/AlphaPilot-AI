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
            reasons.append(f"Trading mode {proposal.mode} does not permit order execution.")
        if (
            proposal.mode == TradingMode.LIMITED_LIVE_AUTO
            and not self.settings.live_trading_enabled
        ):
            reasons.append("Live trading is disabled by configuration.")
        if not self.settings.futu_enable_trade and proposal.mode in {
            TradingMode.PAPER_AUTO,
            TradingMode.LIMITED_LIVE_AUTO,
        }:
            reasons.append("Futu trade gateway is disabled by configuration.")
        if proposal.confidence < self.settings.min_trade_confidence:
            reasons.append(
                f"Confidence {proposal.confidence:.2f} is below "
                f"{self.settings.min_trade_confidence:.2f}."
            )

        age_seconds = (current_time - market_data_as_of).total_seconds()
        if age_seconds < -5:
            reasons.append("Market data timestamp is in the future.")
        elif age_seconds > self.settings.max_market_data_age_seconds:
            reasons.append(
                f"Market data is stale ({age_seconds:.0f}s > "
                f"{self.settings.max_market_data_age_seconds}s)."
            )

        order_pct = proposal.estimated_notional / portfolio.equity
        post_trade_position = portfolio.current_position_pct + (
            order_pct if proposal.side.value == "BUY" else -order_pct
        )
        if post_trade_position > self.settings.max_single_position_pct:
            reasons.append(
                f"Post-trade single-name exposure {post_trade_position:.1%} exceeds "
                f"{self.settings.max_single_position_pct:.1%}."
            )
        if proposal.side.value == "SELL" and post_trade_position < -1e-9:
            reasons.append("Sell proposal exceeds the current long position.")
        if (
            portfolio.sector_position_pct + max(order_pct, 0)
            > self.settings.max_sector_position_pct
        ):
            reasons.append("Post-trade sector exposure would exceed the configured limit.")
        if portfolio.daily_pnl_pct <= -self.settings.max_daily_loss_pct:
            reasons.append("Daily loss kill switch is active.")
        if portfolio.open_orders_for_symbol > 0:
            reasons.append("An open order already exists for this symbol.")
        if proposal.side.value == "BUY" and proposal.estimated_notional > portfolio.cash:
            reasons.append("Insufficient cash for the proposed buy order.")

        requires_confirmation = proposal.mode in {
            TradingMode.CONFIRM_TO_TRADE,
            TradingMode.LIMITED_LIVE_AUTO,
        }
        return RiskDecision(
            approved=not reasons,
            reasons=reasons or ["All configured pre-trade checks passed."],
            evaluated_at=current_time,
            requires_human_confirmation=requires_confirmation,
        )
