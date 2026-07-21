from datetime import UTC, datetime

from alphapilot.core.config import Settings
from alphapilot.domain.models import (
    PortfolioState,
    TradeProposal,
    TradeSide,
    TradingMode,
)
from alphapilot.risk.guardrails import TradeGuardrails


def _proposal(mode: TradingMode) -> TradeProposal:
    return TradeProposal(
        proposal_id="proposal-1",
        idempotency_key="key-1",
        symbol="SH.600000",
        side=TradeSide.BUY,
        quantity=100,
        estimated_notional=1000,
        confidence=0.80,
        market_data_as_of=datetime.now(UTC),
        model_version="test",
        mode=mode,
    )


def _portfolio() -> PortfolioState:
    return PortfolioState(
        equity=100000,
        cash=50000,
        daily_pnl_pct=0,
        current_position_pct=0,
        sector_position_pct=0,
        open_orders_for_symbol=0,
    )


def test_research_mode_rejects_execution() -> None:
    settings = Settings()
    decision = TradeGuardrails(settings).evaluate(_proposal(TradingMode.RESEARCH), _portfolio())
    assert decision.approved is False
    assert any("不允许执行订单" in reason for reason in decision.reasons)


def test_paper_mode_still_needs_gateway_flag() -> None:
    settings = Settings(futu_enable_trade=False)
    decision = TradeGuardrails(settings).evaluate(_proposal(TradingMode.PAPER_AUTO), _portfolio())
    assert decision.approved is False
    assert any("交易网关已被配置禁用" in reason for reason in decision.reasons)
