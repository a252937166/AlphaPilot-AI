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
    baostock_financial_sync_enabled: bool = True
    baostock_socket_timeout_seconds: float = Field(default=2.0, ge=1.0, le=120.0)
    baostock_lock_timeout_seconds: float = Field(default=1.0, ge=0.05, le=120.0)
    # Optional SOCKS5 egress for BaoStock (host:port), e.g. an SSH -D tunnel to a host whose
    # IP is not rate-limited; the ALPHAPILOT_BAOSTOCK_SOCKS5_PROXY process variable wins.
    baostock_socks5_proxy: str | None = None
    # Egress choice: auto = direct first, SOCKS5 proxy when direct fails or is blacklisted;
    # direct / proxy pin one path. Each egress gets its own daily request budget (BaoStock
    # blacklists a source IP around 50,000 requests per day).
    baostock_egress: str = "auto"
    baostock_daily_request_budget: int = Field(default=45000, ge=1)
    valuation_sync_enabled: bool = True
    # Rule-based severe regulatory disclosure screen (立案/处罚事先告知/退市风险).
    severe_disclosure_screen_enabled: bool = True
    # Pre-registered weekly stock-pick forward test: lists and scores are create-only
    # JSON files under this directory; the rules are frozen by evidence outside the repo.
    stock_pick_forward_test_enabled: bool = True
    stock_pick_forward_test_dir: str = "data/stock_picks"
    # Paper accounts that turn the weekly lists into explicit buy/sell actions; the note
    # directory is the owner's vault folder and is set only in the local .env.
    stock_pick_actions_enabled: bool = True
    stock_pick_actions_note_dir: str | None = None
    stock_pick_actions_capital: float = Field(default=1_000_000.0, gt=0)
    stock_pick_actions_top_n: int = Field(default=20, ge=1, le=200)
    # Candidate J (amendment A2): TypeSafe AI's jev picks 20 names from the A and B shortlists.
    stock_pick_jev_enabled: bool = True
    jev_api_key: str | None = None
    jev_model: str = "jev-1.13.0"

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

    # Which chat platform every LLM call goes to. One variable switches the whole
    # process between platforms; both credential sets may sit in .env at once.
    # Registered names live in alphapilot.llm.providers.
    llm_provider: str = "dashscope"

    # DashScope (OpenAI-compatible mode).
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = "qwen3.6-flash"

    # Internal-platform provider. Address and credential are configuration-only:
    # this repository is public, so no default host or path is carried here. The
    # credential is an App ID bound to a model family rather than a per-key API
    # secret. With any of the three absent, the friday provider fails closed
    # rather than falling back to DashScope.
    llm_friday_base_url: str | None = None
    llm_friday_completions_path: str | None = None
    llm_friday_app_id: str | None = None
    llm_friday_model: str = "kimi-k3"
    # Key for the contract's endpoint binding: the registered contract names the
    # platform as provider identity plus a keyed digest of the request URL, so
    # neither the URL nor this salt ever has to be committed. 64 hex characters.
    llm_friday_endpoint_hmac_salt: str | None = None

    # Per-purpose model override; applies to whichever provider is active.
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
