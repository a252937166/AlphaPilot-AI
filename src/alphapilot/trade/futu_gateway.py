from __future__ import annotations

from alphapilot.core.config import Settings


class TradingDisabledError(RuntimeError):
    pass


class FutuTradeGateway:
    """Execution boundary for the audited, disabled-by-default Futu trade surface."""

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
            "futu_trade_query_enabled": self.settings.futu_enable_trade_query,
            "live_trading_enabled": self.settings.live_trading_enabled,
            "order_submission_endpoint_exposed": True,
            "unlock_trade_endpoint_exposed": False,
            "message": (
                "Futu trade calls are audited and disabled by default; unlock_trade is never "
                "exposed and real orders require both live flags and per-request confirmation."
            ),
        }
