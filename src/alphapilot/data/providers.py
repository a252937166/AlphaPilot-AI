from __future__ import annotations

from alphapilot.core.config import Settings
from alphapilot.data.akshare_provider import AKShareMarketDataProvider
from alphapilot.data.base import DataProviderError, MarketDataProvider
from alphapilot.data.futu_provider import FutuMarketDataProvider
from alphapilot.data.mock import MockMarketDataProvider


def build_provider(name: str, settings: Settings) -> MarketDataProvider:
    normalized = name.strip().lower()
    if normalized == "mock":
        return MockMarketDataProvider()
    if normalized == "akshare":
        return AKShareMarketDataProvider()
    if normalized == "futu":
        return FutuMarketDataProvider(settings)
    raise DataProviderError(f"Unknown data provider: {name}")
