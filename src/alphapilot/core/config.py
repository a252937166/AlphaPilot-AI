from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ALPHAPILOT_",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"
    default_data_provider: str = "mock"
    api_cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )

    database_url: str = "postgresql+psycopg://alphapilot:alphapilot@127.0.0.1:5432/alphapilot"
    redis_url: str = "redis://127.0.0.1:6379/0"

    futu_host: str = "127.0.0.1"
    futu_port: int = 11111
    futu_enable_quote: bool = True
    futu_enable_trade: bool = False

    trading_mode: str = "research"
    live_trading_enabled: bool = False
    min_trade_confidence: float = 0.68
    max_single_position_pct: float = 0.10
    max_sector_position_pct: float = 0.30
    max_daily_loss_pct: float = 0.02
    max_market_data_age_seconds: int = 120

    mirofish_base_url: str | None = None
    mirofish_api_key: str | None = None

    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None

    @field_validator("api_cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
