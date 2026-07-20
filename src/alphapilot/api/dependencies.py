from __future__ import annotations

from alphapilot.core.config import Settings, get_settings
from alphapilot.data.base import MarketDataProvider
from alphapilot.data.providers import build_provider


def get_provider(provider_name: str | None = None) -> MarketDataProvider:
    settings = get_settings()
    return build_provider(provider_name or settings.default_data_provider, settings)


def settings_dependency() -> Settings:
    return get_settings()
