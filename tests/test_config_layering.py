"""Tests for layered environment config (config/environments/ overlays)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from core.config import Settings, _deep_merge, load_settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = "config/default.yaml"


def _load(env: str | None) -> Settings:
    """Load settings using the repo's default config + the given env overlay."""
    return load_settings(_DEFAULT_CONFIG, env=env)


# ---------------------------------------------------------------------------
# _deep_merge unit tests
# ---------------------------------------------------------------------------


class TestDeepMerge:
    def test_flat_override(self):
        base = {"a": 1, "b": 2}
        override = {"b": 99, "c": 3}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 99, "c": 3}

    def test_nested_dict_merge(self):
        base = {"capital": {"initial_inr": 500000, "currency": "INR"}}
        override = {"capital": {"initial_inr": 100000}}
        result = _deep_merge(base, override)
        # initial_inr overridden, currency preserved
        assert result["capital"]["initial_inr"] == 100000
        assert result["capital"]["currency"] == "INR"

    def test_deeply_nested(self):
        base = {"a": {"b": {"c": 1, "d": 2}}}
        override = {"a": {"b": {"c": 99}}}
        result = _deep_merge(base, override)
        assert result["a"]["b"]["c"] == 99
        assert result["a"]["b"]["d"] == 2  # untouched

    def test_list_is_replaced_not_merged(self):
        base = {"symbols": ["RELIANCE", "TCS"]}
        override = {"symbols": ["INFY"]}
        result = _deep_merge(base, override)
        assert result["symbols"] == ["INFY"]

    def test_base_unchanged(self):
        base = {"x": {"y": 1}}
        override = {"x": {"y": 2}}
        _deep_merge(base, override)
        assert base["x"]["y"] == 1  # base must not be mutated

    def test_empty_override(self):
        base = {"a": 1}
        result = _deep_merge(base, {})
        assert result == {"a": 1}

    def test_empty_base(self):
        result = _deep_merge({}, {"a": 1})
        assert result == {"a": 1}


# ---------------------------------------------------------------------------
# load_settings with env overlays
# ---------------------------------------------------------------------------


class TestDevOverlay:
    def test_capital_reduced(self):
        settings = _load("dev")
        assert settings.capital.initial_inr == 100000

    def test_risk_per_trade_reduced(self):
        settings = _load("dev")
        assert settings.risk.per_trade_risk_pct == 0.5

    def test_max_open_positions_reduced(self):
        settings = _load("dev")
        assert settings.risk.max_open_positions == 3

    def test_daily_loss_circuit_more_permissive(self):
        settings = _load("dev")
        assert settings.risk.daily_loss_circuit_pct == 5.0

    def test_logging_level_debug(self):
        settings = _load("dev")
        assert settings.logging.level == "DEBUG"

    def test_notifications_disabled(self):
        settings = _load("dev")
        assert settings.notifications.telegram_enabled is False
        assert settings.notifications.email_enabled is False

    def test_separate_db_path(self):
        settings = _load("dev")
        assert settings.persistence.sqlite_path == "data/agent_dev.db"

    def test_non_overridden_fields_preserved(self):
        """Fields not mentioned in dev.yaml must retain their default values."""
        settings = _load("dev")
        # env=None with no APP_ENV set uses the base config (no overlay)
        assert settings.market.exchange == "NSE"
        assert settings.backtest_gate.min_sharpe == 1.2


class TestStagingOverlay:
    def test_uses_staging_db_path(self):
        settings = _load("staging")
        assert settings.persistence.sqlite_path == "data/agent_staging.db"

    def test_capital_is_500k(self):
        settings = _load("staging")
        assert settings.capital.initial_inr == 500000

    def test_logging_info(self):
        settings = _load("staging")
        assert settings.logging.level == "INFO"


class TestProdOverlay:
    def test_mode_is_live(self):
        settings = _load("prod")
        assert settings.mode == "live"

    def test_uses_prod_db_path(self):
        settings = _load("prod")
        assert settings.persistence.sqlite_path == "data/agent_prod.db"

    def test_logging_warning(self):
        settings = _load("prod")
        assert settings.logging.level == "WARNING"


class TestUnknownEnv:
    """An unknown environment name should fall back to the base config (no overlay)."""

    def test_unknown_env_uses_defaults(self):
        # The overlay file for "nonexistent" does not exist; load_settings must
        # silently skip it and return the base defaults.
        settings = load_settings(_DEFAULT_CONFIG, env="nonexistent")
        # Mode comes from default.yaml, not any overlay
        assert settings.mode == "paper"
        assert settings.capital.initial_inr == 500000  # default

    def test_unknown_env_does_not_raise(self):
        # Must not raise even if the env file doesn't exist
        try:
            load_settings(_DEFAULT_CONFIG, env="does_not_exist")
        except Exception as exc:
            pytest.fail(f"load_settings raised unexpectedly: {exc}")


class TestAppEnvVariable:
    """APP_ENV environment variable should be the fallback when env=None."""

    def test_app_env_staging_is_respected(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "staging")
        settings = load_settings(_DEFAULT_CONFIG, env=None)
        assert settings.persistence.sqlite_path == "data/agent_staging.db"

    def test_app_env_dev_is_respected(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "dev")
        settings = load_settings(_DEFAULT_CONFIG, env=None)
        assert settings.capital.initial_inr == 100000

    def test_explicit_env_overrides_app_env(self, monkeypatch):
        """Passing env= explicitly must take precedence over APP_ENV."""
        monkeypatch.setenv("APP_ENV", "staging")
        settings = load_settings(_DEFAULT_CONFIG, env="dev")
        # dev overlay should win, not staging
        assert settings.capital.initial_inr == 100000
        assert settings.persistence.sqlite_path == "data/agent_dev.db"

    def test_no_app_env_uses_base_config(self, monkeypatch):
        """When APP_ENV is unset and env=None, base config is used with no overlay."""
        monkeypatch.delenv("APP_ENV", raising=False)
        settings = load_settings(_DEFAULT_CONFIG, env=None)
        # Base default.yaml has 500000 and per_trade_risk_pct=1.0; dev overlay halves them
        assert settings.capital.initial_inr == 500000
        assert settings.risk.per_trade_risk_pct == 1.0
        assert settings.risk.max_open_positions == 5
