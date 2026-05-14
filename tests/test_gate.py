"""Tests for the strict backtest gate.

The gate is a safety guarantee — if it's broken, we'd be deploying untested
strategies to live capital. These tests cover threshold logic, file I/O,
and the live-mode allow check.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backtest.gate import (
    GATE_MAX_AGE_DAYS,
    GateResult,
    evaluate_gate,
    is_live_allowed,
    read_gate_result,
    write_gate_result,
)
from backtest.metrics import BacktestMetrics
from core.config import BacktestGateCfg


def _cfg(**overrides):
    base = dict(
        min_sharpe=1.2,
        max_drawdown_pct=15.0,
        min_win_rate=45.0,
        min_profit_factor=1.5,
        min_trades=100,
        walk_forward_years=3,
    )
    base.update(overrides)
    return BacktestGateCfg(**base)


def _metrics(**overrides):
    base = dict(
        n_trades=120, win_rate_pct=50.0, profit_factor=1.8,
        avg_win=200, avg_loss=-100, total_pnl=10000, sharpe=1.5,
        sortino=1.8, max_drawdown_pct=10.0, cagr_pct=15.0, expectancy=83.0,
        starting_equity=100_000, ending_equity=110_000, period_days=1095,
    )
    base.update(overrides)
    return BacktestMetrics(**base)


def test_passing_metrics_pass_gate():
    g = evaluate_gate(_metrics(), _cfg(), walk_forward_years=3)
    assert g.passed
    assert all(c.pass_ for c in g.checks)
    assert g.failures == []


def test_low_sharpe_fails():
    g = evaluate_gate(_metrics(sharpe=0.9), _cfg(), walk_forward_years=3)
    assert not g.passed
    assert "sharpe" in g.failures


def test_high_drawdown_fails():
    g = evaluate_gate(_metrics(max_drawdown_pct=20.0), _cfg(), walk_forward_years=3)
    assert not g.passed
    assert "max_drawdown_pct" in g.failures


def test_too_few_trades_fails():
    g = evaluate_gate(_metrics(n_trades=50), _cfg(), walk_forward_years=3)
    assert not g.passed
    assert "n_trades" in g.failures


def test_short_walk_forward_fails():
    g = evaluate_gate(_metrics(), _cfg(), walk_forward_years=1.0)
    assert not g.passed
    assert any("walk_forward_years_too_short" in f for f in g.failures)


def test_write_and_read_round_trip(tmp_path: Path):
    g = evaluate_gate(_metrics(), _cfg(), walk_forward_years=3)
    p = tmp_path / "gate.json"
    write_gate_result(g, p)
    data = read_gate_result(p)
    assert data is not None
    assert data["passed"] is True


def test_read_missing_returns_none(tmp_path: Path):
    assert read_gate_result(tmp_path / "missing.json") is None


def test_is_live_allowed_when_no_gate(tmp_path: Path):
    allowed, reason = is_live_allowed(tmp_path / "missing.json")
    assert not allowed
    assert "no gate result" in reason


def test_is_live_allowed_when_failed(tmp_path: Path):
    g = evaluate_gate(_metrics(sharpe=0.5), _cfg(), walk_forward_years=3)
    p = tmp_path / "gate.json"
    write_gate_result(g, p)
    allowed, reason = is_live_allowed(p)
    assert not allowed
    assert "not passed" in reason


def test_is_live_allowed_when_stale(tmp_path: Path):
    g = evaluate_gate(_metrics(), _cfg(), walk_forward_years=3)
    p = tmp_path / "gate.json"
    write_gate_result(g, p)
    data = json.loads(p.read_text())
    old_ts = (datetime.now(timezone.utc) - timedelta(days=GATE_MAX_AGE_DAYS + 5)).isoformat()
    data["timestamp"] = old_ts
    p.write_text(json.dumps(data))
    allowed, reason = is_live_allowed(p)
    assert not allowed
    assert "too old" in reason


def test_is_live_allowed_when_passing(tmp_path: Path):
    g = evaluate_gate(_metrics(), _cfg(), walk_forward_years=3)
    p = tmp_path / "gate.json"
    write_gate_result(g, p)
    allowed, reason = is_live_allowed(p)
    assert allowed
    assert reason == "ok"
