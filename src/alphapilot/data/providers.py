from __future__ import annotations

from alphapilot.core.config import Settings
from alphapilot.data.akshare_provider import AKShareMarketDataProvider
from alphapilot.data.baostock_provider import BaoStockMarketDataProvider
from alphapilot.data.base import DataProviderError, MarketDataProvider
from alphapilot.data.futu_provider import FutuMarketDataProvider
from alphapilot.data.mock import MockMarketDataProvider
from alphapilot.data.router import FailoverMarketDataProvider


def _build_simple(name: str, settings: Settings) -> MarketDataProvider:
    if name == "mock":
        return MockMarketDataProvider()
    if name == "akshare":
        return AKShareMarketDataProvider()
    if name == "baostock":
        return BaoStockMarketDataProvider()
    if name == "futu":
        return FutuMarketDataProvider(settings)
    raise DataProviderError(f"Unknown data provider: {name}")


def build_provider(name: str, settings: Settings) -> MarketDataProvider:
    normalized = name.strip().lower()
    if normalized != "auto":
        return _build_simple(normalized, settings)
    bars_chain = [_build_simple(item, settings) for item in settings.daily_bars_provider_chain]
    snapshot_chain = [_build_simple(item, settings) for item in settings.snapshot_provider_chain]
    return FailoverMarketDataProvider(bars_chain, snapshot_chain)
