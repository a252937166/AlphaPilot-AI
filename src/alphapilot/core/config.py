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
    default_data_provider: str = "auto"
    api_cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )

    # SQLite works out of the box; point at PostgreSQL via ALPHAPILOT_DATABASE_URL
    # (for example postgresql+psycopg://alphapilot:alphapilot@127.0.0.1:5432/alphapilot).
    database_url: str = "sqlite:///data/alphapilot.db"
    database_echo: bool = False
    redis_url: str = "redis://127.0.0.1:6379/0"
    scheduler_enabled: bool = False
    market_poll_enabled: bool = False
    valuation_sync_enabled: bool = True

    # Failover order used by the "auto" composite provider.
    daily_bars_provider_chain: list[str] = Field(
        default_factory=lambda: ["baostock", "akshare", "futu"]
    )
    snapshot_provider_chain: list[str] = Field(default_factory=lambda: ["futu", "akshare"])
    universe_file: str = "config/universe.example.yaml"
    factor_weights_file: str = "config/factor_weights.yaml"
    tushare_token: str | None = None

    # cninfo / 深证信 WebAPI. Credentials must come from the local .env or the
    # process environment only; they are never committed to the repository.
    cninfo_access_key: str | None = None
    cninfo_access_secret: str | None = None
    cninfo_base_url: str = "https://webapi.cninfo.com.cn"
    cninfo_announcement_base_url: str = "http://www.cninfo.com.cn"

    futu_host: str = "127.0.0.1"
    futu_port: int = 11111
    futu_enable_quote: bool = True
    futu_enable_trade_query: bool = False
    futu_enable_account_mutation: bool = False
    futu_enable_trade: bool = False
    futu_security_firm: str = "FUTUSECURITIES"

    trading_mode: str = "research"
    live_trading_enabled: bool = False
    paper_trading_enabled: bool = False
    paper_auto_trading_enabled: bool = False
    paper_auto_max_orders_per_day: int = Field(default=3, ge=1, le=20)
    paper_auto_max_order_notional_pct: float = Field(default=0.02, gt=0, le=0.10)
    trading_halted: bool = False
    demo_equity: float = Field(default=1_000_000.0, gt=0)
    min_trade_confidence: float = 0.68
    max_single_position_pct: float = 0.10
    max_sector_position_pct: float = 0.30
    max_daily_loss_pct: float = 0.02
    max_market_data_age_seconds: int = 120

    mirofish_base_url: str | None = None
    mirofish_api_key: str | None = None

    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = "qwen3.6-flash"
    llm_purpose_models: dict[str, str] = Field(default_factory=dict)
    llm_polish_feed: bool = False

    @field_validator(
        "api_cors_origins",
        "daily_bars_provider_chain",
        "snapshot_provider_chain",
        mode="before",
    )
    @classmethod
    def split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
