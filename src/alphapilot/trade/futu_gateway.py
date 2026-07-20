from __future__ import annotations

from alphapilot.core.config import Settings


class TradingDisabledError(RuntimeError):
    pass


class FutuTradeGateway:
    """Execution boundary. Order submission is intentionally not exposed in the MVP API."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def assert_execution_enabled(self, live: bool) -> None:
        if not self.settings.futu_enable_trade:
            raise TradingDisabledError("Futu trade gateway is disabled.")
        if live and not self.settings.live_trading_enabled:
            raise TradingDisabledError("Live trading is disabled.")

    def execution_status(self) -> dict[str, object]:
        return {
            "futu_trade_enabled": self.settings.futu_enable_trade,
            "live_trading_enabled": self.settings.live_trading_enabled,
            "order_submission_endpoint_exposed": False,
            "message": (
                "MVP evaluates trade proposals only; order submission requires a later "
                "audited gateway."
            ),
        }
