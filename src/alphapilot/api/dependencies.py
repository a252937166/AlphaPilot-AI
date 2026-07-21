from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from alphapilot.cninfo.client import CninfoClient, get_cninfo_client
from alphapilot.core.config import Settings, get_settings
from alphapilot.data.base import MarketDataProvider
from alphapilot.data.providers import build_provider
from alphapilot.db.engine import get_session
from alphapilot.futu.client import FutuClient, get_futu_client


def get_provider(provider_name: str | None = None) -> MarketDataProvider:
    settings = get_settings()
    return build_provider(provider_name or settings.default_data_provider, settings)


def settings_dependency() -> Settings:
    return get_settings()


def futu_client_dependency() -> FutuClient:
    return get_futu_client()


def cninfo_client_dependency() -> CninfoClient:
    return get_cninfo_client()


def db_session_dependency() -> Iterator[Session]:
    with get_session() as session:
        yield session
