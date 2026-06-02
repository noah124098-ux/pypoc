"""Configuration loader. Merges YAML defaults with environment variables from .env."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


class CapitalCfg(BaseModel):
    initial_inr: float
    currency: str = "INR"


class MarketCfg(BaseModel):
    exchange: str
    segment: str
    trading_start: str
    trading_end: str
    no_new_entries_after: str
    intraday_squareoff_at: str


class RiskCfg(BaseModel):
    per_trade_risk_pct: float
    max_position_pct: float
    max_open_positions: int
    daily_loss_circuit_pct: float
    drawdown_circuit_pct: float
    liquidity_max_pct_of_adv: float
    max_spread_pct: float
    black_swan_nifty_move_pct: float
    black_swan_vix_jump_pct: float
    stale_tick_seconds: int


class RegimeCfg(BaseModel):
    adx_period: int
    adx_trend_threshold: float
    bb_width_range_threshold: float
    vix_volatile_threshold: float


class ExecutionCfg(BaseModel):
    slippage_bps: float
    brokerage_per_order_inr: float
    stt_pct: float
    exchange_txn_pct: float
    gst_pct: float
    signal_cooldown_minutes: int


class BacktestGateCfg(BaseModel):
    min_sharpe: float
    max_drawdown_pct: float
    min_win_rate: float
    min_profit_factor: float
    min_trades: int
    walk_forward_years: int


class DataCfg(BaseModel):
    primary_feed: str
    reconnect_max_attempts: int
    reconnect_backoff_seconds: int


class PersistenceCfg(BaseModel):
    sqlite_path: str
    tick_parquet_dir: str


class LLMCfg(BaseModel):
    eod_reviewer_model: str
    news_scorer_model: str
    enable_eod_review: bool
    auto_apply_within_safe_bounds: bool


class NotificationsCfg(BaseModel):
    telegram_enabled: bool
    email_enabled: bool
    dashboard_enabled: bool
    dashboard_port: int


class LoggingCfg(BaseModel):
    level: str
    file: str
    json_log_file: str
    max_bytes: int = 10_485_760   # 10 MB per file
    backup_count: int = 5         # number of rotated backups to keep


class UniverseCfg(BaseModel):
    source: str
    symbols: list[str] = Field(default_factory=list)


class Settings(BaseModel):
    mode: str
    timezone: str
    capital: CapitalCfg
    market: MarketCfg
    universe: UniverseCfg
    risk: RiskCfg
    regime: RegimeCfg
    strategies: dict[str, dict[str, Any]]
    execution: ExecutionCfg
    backtest_gate: BacktestGateCfg
    data: DataCfg
    persistence: PersistenceCfg
    llm: LLMCfg
    notifications: NotificationsCfg
    logging: LoggingCfg


class Secrets(BaseModel):
    """Secrets pulled from environment. Empty strings allowed for unused integrations."""

    # Data-feed app credentials (DATA-ONLY — never used for order placement).
    angel_one_api_key: str = ""
    angel_one_api_secret: str = ""
    angel_one_client_code: str = ""
    angel_one_password: str = ""
    angel_one_totp_secret: str = ""

    # Live-broker app credentials (SEPARATE app from the data feed).
    # These are intentionally empty until Phase 7 live-broker integration.
    # NEVER populate these with the data-feed credentials above.
    angel_one_live_api_key: str = ""
    angel_one_live_client_code: str = ""
    angel_one_live_password: str = ""
    angel_one_live_totp_secret: str = ""

    # Upstox alternative data feed (stub — requires upstox_api2 package).
    upstox_api_key: str = ""
    upstox_access_token: str = ""

    anthropic_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""

    @classmethod
    def from_env(cls) -> "Secrets":
        load_dotenv(override=False)
        return cls(
            angel_one_api_key=os.getenv("ANGEL_ONE_API_KEY", ""),
            angel_one_api_secret=os.getenv("ANGEL_ONE_API_SECRET", ""),
            angel_one_client_code=os.getenv("ANGEL_ONE_CLIENT_CODE", ""),
            angel_one_password=os.getenv("ANGEL_ONE_PASSWORD", ""),
            angel_one_totp_secret=os.getenv("ANGEL_ONE_TOTP_SECRET", ""),
            # Live-broker vars — separate app, separate credentials.
            angel_one_live_api_key=os.getenv("ANGEL_ONE_LIVE_API_KEY", ""),
            angel_one_live_client_code=os.getenv("ANGEL_ONE_LIVE_CLIENT_CODE", ""),
            angel_one_live_password=os.getenv("ANGEL_ONE_LIVE_PASSWORD", ""),
            angel_one_live_totp_secret=os.getenv("ANGEL_ONE_LIVE_TOTP_SECRET", ""),
            upstox_api_key=os.getenv("UPSTOX_API_KEY", ""),
            upstox_access_token=os.getenv("UPSTOX_ACCESS_TOKEN", ""),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_user=os.getenv("SMTP_USER", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            email_from=os.getenv("EMAIL_FROM", ""),
            email_to=os.getenv("EMAIL_TO", ""),
        )


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict.

    Nested dicts are merged key-by-key. Any other type (str, int, list …) in
    *override* replaces the corresponding value in *base* outright.
    """
    result = base.copy()
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_settings(path: str | Path = "config/default.yaml", env: str | None = None) -> Settings:
    """Load settings from *path*, then layer environment-specific overrides on top.

    Resolution order (later values win):
      1. *path* (default: config/default.yaml)
      2. <config_dir>/environments/<env>.yaml  (sibling of the config directory,
         resolved relative to the parent of *path*)
      3. .env / environment variables    (handled by Secrets separately)

    *env* defaults to the ``APP_ENV`` environment variable. When ``APP_ENV``
    is not set, no overlay is applied and the base config is used as-is.
    Set ``APP_ENV=dev`` explicitly for lighter capital / verbose logging.

    The environment overlay is looked up relative to the *config file's own
    directory*, not the process working directory, so tests that pass an
    absolute tmp-path config get a predictable result even when no
    environments/ folder exists next to that file.
    """
    config_file = Path(path)
    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))

    if env is None:
        env = os.getenv("APP_ENV", None)  # None = use base config only

    # Resolve the overlay path relative to the directory that contains the
    # config file (e.g. config/ → config/environments/dev.yaml).
    env_config_path = config_file.parent / "environments" / f"{env}.yaml"
    if env_config_path.exists():
        env_raw = yaml.safe_load(env_config_path.read_text(encoding="utf-8"))
        if env_raw:  # guard against empty file
            raw = _deep_merge(raw, env_raw)

    return Settings.model_validate(raw)


def reload_settings(path: str = "config/default.yaml", env: str | None = None) -> Settings:
    """Reload settings from YAML — call from orchestrator tick to pick up changes."""
    return load_settings(path, env=env)
