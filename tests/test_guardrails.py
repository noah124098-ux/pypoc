"""Guardrail tests — every rule must have at least one allow + one reject case.

These tests are the most important in the project. CI must fail loudly if any of these
break, because a broken guardrail can blow up real capital.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.types import Position, Side


def test_baseline_signal_allowed(guardrails, base_signal, portfolio, market_ctx):
    decision = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert decision.allow, decision


def test_qty_zero_rejected(guardrails, base_signal, portfolio, market_ctx):
    decision = guardrails.check(base_signal, qty=0, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "qty_positive"


def test_stop_above_entry_long_rejected(guardrails, base_signal, portfolio, market_ctx):
    base_signal.stop_loss = base_signal.entry_price + 1
    decision = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "stop_loss_required"


def test_no_stop_loss_rejected(guardrails, base_signal, portfolio, market_ctx):
    base_signal.stop_loss = 0
    decision = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "stop_loss_required"


def test_outside_market_window_rejected(guardrails, base_signal, portfolio, market_ctx):
    market_ctx.now = market_ctx.now.replace(hour=8, minute=0)
    decision = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "market_window"


def test_after_no_entry_cutoff_rejected(guardrails, base_signal, portfolio, market_ctx):
    market_ctx.now = market_ctx.now.replace(hour=14, minute=46)
    decision = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "market_window"


def test_stale_data_rejected(guardrails, base_signal, portfolio, market_ctx):
    market_ctx.last_tick_age_seconds = 30
    decision = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "stale_data"


def test_black_swan_nifty_rejected(guardrails, base_signal, portfolio, market_ctx):
    market_ctx.nifty_change_pct_15m = -2.5
    decision = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "black_swan_nifty"


def test_black_swan_vix_rejected(guardrails, base_signal, portfolio, market_ctx):
    market_ctx.vix_change_pct_15m = 25
    decision = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "black_swan_vix"


def test_max_open_positions_rejected(guardrails, base_signal, portfolio, market_ctx):
    portfolio.open_positions = [
        Position(symbol=f"S{i}", qty=10, avg_price=100, stop_loss=95,
                 target=110, strategy="x", opened_at=datetime.utcnow())
        for i in range(5)
    ]
    decision = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "max_open_positions"


def test_per_trade_risk_exceeded_rejected(guardrails, base_signal, portfolio, market_ctx):
    # equity 100k, 1% = 1000 risk budget. Risk per share = 20. Max qty = 50.
    decision = guardrails.check(base_signal, qty=200, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "per_trade_risk"


def test_max_position_size_exceeded_rejected(guardrails, base_signal, portfolio, market_ctx):
    # equity 100k, max 20% = 20k position. At 1300/share, max ~15 shares.
    base_signal.stop_loss = base_signal.entry_price - 1  # tiny risk so per-trade-risk doesn't trip
    decision = guardrails.check(base_signal, qty=100, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "max_position_size"


def test_liquidity_exceeded_rejected(guardrails, base_signal, portfolio, market_ctx):
    # Use a low-priced symbol so size+risk caps don't fire first; only liquidity should reject.
    base_signal.symbol = "PENNY"
    base_signal.entry_price = 50.0
    base_signal.stop_loss = 49.99   # tiny risk so per_trade_risk allows the qty
    market_ctx.avg_daily_volumes = {"PENNY": 1_000}  # 1% of ADV = 10 shares max
    market_ctx.spread_pct_by_symbol = {"PENNY": 0.05}
    decision = guardrails.check(base_signal, qty=50, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "liquidity"


def test_spread_too_wide_rejected(guardrails, base_signal, portfolio, market_ctx):
    market_ctx.spread_pct_by_symbol["RELIANCE"] = 0.5
    decision = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "spread"


# ---------------------------------------------------------------------------
# LIVE-mode fail-closed: missing ADV/spread data must REJECT in live, but the
# default (paper/backtest) Guardrails stays permissive so the gate is unaffected.
# ---------------------------------------------------------------------------

def _live_guardrails(risk_cfg, market_cfg, execution_cfg):
    from core.risk.guardrails import Guardrails
    return Guardrails(risk_cfg, market_cfg, execution_cfg, live_mode=True)


def test_paper_mode_permissive_on_missing_adv(guardrails, base_signal, portfolio, market_ctx):
    """Default (paper) Guardrails must ALLOW when ADV data is absent — gate relies on this."""
    market_ctx.avg_daily_volumes = {}            # no ADV
    market_ctx.spread_pct_by_symbol = {"RELIANCE": 0.05}
    decision = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert decision.allow, decision


def test_paper_mode_permissive_on_missing_spread(guardrails, base_signal, portfolio, market_ctx):
    """Default (paper) Guardrails must ALLOW when spread data is absent."""
    market_ctx.spread_pct_by_symbol = {}         # no spread tick
    decision = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert decision.allow, decision


def test_live_mode_rejects_missing_adv(risk_cfg, market_cfg, execution_cfg, base_signal, portfolio, market_ctx):
    """LIVE Guardrails must REJECT when ADV data is missing (can't size safely)."""
    g = _live_guardrails(risk_cfg, market_cfg, execution_cfg)
    market_ctx.avg_daily_volumes = {}            # no ADV
    market_ctx.spread_pct_by_symbol = {"RELIANCE": 0.05}
    decision = g.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "liquidity"


def test_live_mode_rejects_missing_spread(risk_cfg, market_cfg, execution_cfg, base_signal, portfolio, market_ctx):
    """LIVE Guardrails must REJECT when spread data is missing (book unknown)."""
    g = _live_guardrails(risk_cfg, market_cfg, execution_cfg)
    market_ctx.avg_daily_volumes = {"RELIANCE": 10_000_000}  # ample ADV so only spread fires
    market_ctx.spread_pct_by_symbol = {}         # no spread tick
    decision = g.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "spread"


def test_live_mode_allows_when_data_present(risk_cfg, market_cfg, execution_cfg, base_signal, portfolio, market_ctx):
    """LIVE Guardrails must ALLOW normally when both ADV and spread are present and OK."""
    g = _live_guardrails(risk_cfg, market_cfg, execution_cfg)
    market_ctx.avg_daily_volumes = {"RELIANCE": 10_000_000}
    market_ctx.spread_pct_by_symbol = {"RELIANCE": 0.05}
    decision = g.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert decision.allow, decision


def test_daily_loss_circuit_rejected(guardrails, base_signal, portfolio, market_ctx):
    portfolio.equity = 96_000  # -4% from 100k
    decision = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "daily_loss_circuit"


def test_drawdown_circuit_rejected(guardrails, base_signal, portfolio, market_ctx):
    portfolio.peak_equity = 110_000
    portfolio.equity = 95_000  # -13.6% drawdown
    portfolio.starting_equity_today = 95_000  # neutralize daily-loss check
    decision = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "drawdown_circuit"


def test_signal_cooldown_rejected(guardrails, base_signal, portfolio, market_ctx):
    portfolio.last_exit_by_symbol = {"RELIANCE": market_ctx.now - timedelta(minutes=5)}
    decision = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "signal_cooldown"


def test_global_halt_rejected(guardrails, base_signal, portfolio, market_ctx):
    portfolio.halted = True
    portfolio.halt_reason = "test"
    decision = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert not decision.allow
    assert decision.rule == "global_halt"
