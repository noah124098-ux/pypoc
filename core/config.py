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


def load_settings(path: str | Path = "config/default.yaml") -> Settings:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Settings.model_validate(raw)
