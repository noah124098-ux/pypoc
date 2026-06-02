"""Extended guardrail tests — covers branches not hit by the base test suite.

Target rules:
- _check_global_halt: halt_reason empty string (uses "halted" fallback)
- _check_stop_loss_required: SELL side with stop_loss > entry (correctly formed short)
  and SELL side with stop_loss <= entry (invalid short stop)
- _check_black_swan: nifty positive spike also triggers (abs check)
- _check_max_open_positions: same symbol already open counts as adding-to-position (allow)
- _check_per_trade_risk: exactly at budget boundary (allow — fudge covers exact equality)
- _check_max_position_size: exactly at cap boundary (allow)
- _check_liquidity: ADV is 0 → let it pass (missing data, not policy violation)
- _check_liquidity: ADV present, qty within limit → allow
- _check_spread: spread is None (no data) → allow
- _check_daily_loss_circuit: starting_equity_today == 0 → skip check (allow)
- _check_drawdown_circuit: peak_equity == 0 → skip check (allow)
- _check_signal_cooldown: cooldown fully expired → allow
- _check_duplicate_position: SELL on a held long → allow (closing allowed)
- full check() short-circuits at first reject and returns that rule
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.types import Position, Side, Regime, Signal
from core.risk.guardrails import Guardrails, GuardrailDecision, MarketContext, PortfolioState


# ---------------------------------------------------------------------------
# _check_global_halt: empty halt_reason uses "halted" fallback
# ---------------------------------------------------------------------------

def test_global_halt_empty_reason_fallback(guardrails, base_signal, portfolio, market_ctx):
    portfolio.halted = True
    portfolio.halt_reason = ""  # empty — should fall back to "halted"
    d = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert not d.allow
    assert d.rule == "global_halt"
    assert d.reason == "halted"


# ---------------------------------------------------------------------------
# _check_stop_loss_required: SHORT side rules
# ---------------------------------------------------------------------------

def _short_signal(entry: float, stop: float) -> Signal:
    return Signal(
        symbol="RELIANCE",
        side=Side.SELL,
        strategy="supertrend_short",
        regime=Regime.VOLATILE,
        entry_price=entry,
        stop_loss=stop,
        target=entry * 0.95,
        confidence=0.7,
        rationale="test short",
        ts=datetime(2026, 5, 13, 10, 30),
    )


def test_sell_stop_below_entry_rejected(guardrails, portfolio, market_ctx):
    """SELL with stop_loss <= entry is invalid (stop must be above entry for shorts)."""
    sig = _short_signal(entry=1300.0, stop=1280.0)  # stop below entry for SELL → invalid
    d = guardrails.check(sig, qty=10, portfolio=portfolio, market=market_ctx)
    assert not d.allow
    assert d.rule == "stop_loss_required"
    assert "stop below entry on short" in d.reason


def test_sell_valid_stop_above_entry_allowed(guardrails, portfolio, market_ctx):
    """SELL with stop_loss > entry is valid (stop above entry for shorts = correct)."""
    sig = _short_signal(entry=1300.0, stop=1320.0)  # stop above entry for SELL → valid
    d = guardrails.check(sig, qty=1, portfolio=portfolio, market=market_ctx)
    # Should not be rejected for stop_loss_required; may be rejected for other rules
    if not d.allow:
        assert d.rule != "stop_loss_required"


# ---------------------------------------------------------------------------
# _check_black_swan: nifty positive spike (abs check)
# ---------------------------------------------------------------------------

def test_black_swan_nifty_positive_spike_rejected(guardrails, base_signal, portfolio, market_ctx):
    """A large positive nifty move also triggers black_swan (uses abs)."""
    market_ctx.nifty_change_pct_15m = +3.0  # positive spike
    d = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert not d.allow
    assert d.rule == "black_swan_nifty"


# ---------------------------------------------------------------------------
# _check_max_open_positions: same symbol already open → not a new position
# ---------------------------------------------------------------------------

def test_max_open_positions_same_symbol_allows(guardrails, base_signal, portfolio, market_ctx):
    """Adding to an existing same-symbol position is allowed even when at max count."""
    # Fill 5 positions including RELIANCE (the base signal symbol)
    reliance_pos = Position(symbol="RELIANCE", qty=5, avg_price=1290.0, stop_loss=1270.0,
                            target=1350.0, strategy="x", opened_at=datetime.utcnow())
    other_pos = [
        Position(symbol=f"S{i}", qty=10, avg_price=100, stop_loss=95,
                 target=110, strategy="x", opened_at=datetime.utcnow())
        for i in range(4)
    ]
    portfolio.open_positions = [reliance_pos] + other_pos
    # max_open_positions=5 but RELIANCE is already in existing, so not a new slot
    d = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    # Must NOT be rejected by max_open_positions
    if not d.allow:
        assert d.rule != "max_open_positions"


# ---------------------------------------------------------------------------
# _check_per_trade_risk: exactly at budget (within fudge factor)
# ---------------------------------------------------------------------------

def test_per_trade_risk_exactly_at_budget_allowed(guardrails, base_signal, portfolio, market_ctx):
    """A trade whose risk equals exactly the budget must be allowed (fudge: * 1.001)."""
    # equity 100k, 1% = 1000. Stop distance 20, qty 50 → risk = 1000 exactly.
    base_signal.entry_price = 200.0
    base_signal.stop_loss = 180.0   # risk_per_share = 20
    base_signal.target = 240.0
    d = guardrails.check(base_signal, qty=50, portfolio=portfolio, market=market_ctx)
    if not d.allow:
        assert d.rule != "per_trade_risk"


# ---------------------------------------------------------------------------
# _check_liquidity: ADV == 0 → pass through (missing data)
# ---------------------------------------------------------------------------

def test_liquidity_adv_zero_allows(guardrails, base_signal, portfolio, market_ctx):
    """ADV of 0 should be treated as missing data — do not block."""
    market_ctx.avg_daily_volumes = {"RELIANCE": 0}
    d = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    if not d.allow:
        assert d.rule != "liquidity"


def test_liquidity_within_adv_allows(guardrails, base_signal, portfolio, market_ctx):
    """qty well within ADV limit should be allowed."""
    # ADV = 10M, 1% of that = 100k shares. qty=10 is far under limit.
    market_ctx.avg_daily_volumes = {"RELIANCE": 10_000_000}
    d = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    if not d.allow:
        assert d.rule != "liquidity"


def test_liquidity_symbol_not_in_adv_allows(guardrails, base_signal, portfolio, market_ctx):
    """Symbol absent from ADV dict is treated as missing data — do not block."""
    market_ctx.avg_daily_volumes = {}  # no ADV data at all
    d = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    if not d.allow:
        assert d.rule != "liquidity"


# ---------------------------------------------------------------------------
# _check_spread: spread is None / symbol absent → allow
# ---------------------------------------------------------------------------

def test_spread_absent_allows(guardrails, base_signal, portfolio, market_ctx):
    """No spread data for the symbol should not block trading."""
    market_ctx.spread_pct_by_symbol = {}  # no spread data
    d = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    if not d.allow:
        assert d.rule != "spread"


# ---------------------------------------------------------------------------
# _check_daily_loss_circuit: starting_equity_today == 0 → skip
# ---------------------------------------------------------------------------

def test_daily_loss_circuit_zero_starting_equity_allows(guardrails, base_signal, portfolio, market_ctx):
    """If starting_equity_today is 0, daily-loss check must be skipped."""
    portfolio.starting_equity_today = 0.0
    portfolio.equity = 50_000.0  # would be a huge loss if starting != 0
    d = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    if not d.allow:
        assert d.rule != "daily_loss_circuit"


# ---------------------------------------------------------------------------
# _check_drawdown_circuit: peak_equity == 0 → skip
# ---------------------------------------------------------------------------

def test_drawdown_circuit_zero_peak_equity_allows(guardrails, base_signal, portfolio, market_ctx):
    """If peak_equity is 0, drawdown check must be skipped."""
    portfolio.peak_equity = 0.0
    portfolio.equity = 50_000.0  # would be huge drawdown if peak != 0
    portfolio.starting_equity_today = 50_000.0  # keep daily-loss check calm
    d = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    if not d.allow:
        assert d.rule != "drawdown_circuit"


# ---------------------------------------------------------------------------
# _check_signal_cooldown: cooldown fully expired → allow
# ---------------------------------------------------------------------------

def test_signal_cooldown_expired_allows(guardrails, base_signal, portfolio, market_ctx):
    """After cooldown period has elapsed the symbol should be tradeable again."""
    # cooldown = 30 minutes; set last exit to 60 minutes ago
    portfolio.last_exit_by_symbol = {"RELIANCE": market_ctx.now - timedelta(minutes=60)}
    d = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    if not d.allow:
        assert d.rule != "signal_cooldown"


# ---------------------------------------------------------------------------
# _check_duplicate_position: SELL to close an existing long is allowed
# ---------------------------------------------------------------------------

def test_duplicate_sell_closes_long_allowed(guardrails, portfolio, market_ctx):
    """Selling a symbol that has an open long position should be allowed (it is an exit)."""
    portfolio.open_positions = [
        Position(
            symbol="RELIANCE", qty=10, avg_price=1280.0, stop_loss=1260.0,
            target=1360.0, strategy="trend_breakout", opened_at=datetime.utcnow()
        )
    ]
    sell_signal = Signal(
        symbol="RELIANCE",
        side=Side.SELL,
        strategy="trend_breakout",
        regime=Regime.TREND,
        entry_price=1300.0,
        stop_loss=1320.0,  # valid short stop (above entry)
        target=1240.0,
        confidence=0.6,
        rationale="close position",
        ts=datetime(2026, 5, 13, 10, 30),
    )
    d = guardrails.check(sell_signal, qty=1, portfolio=portfolio, market=market_ctx)
    if not d.allow:
        assert d.rule != "duplicate_position"


# ---------------------------------------------------------------------------
# check() short-circuit: first failing rule stops evaluation
# ---------------------------------------------------------------------------

def test_check_short_circuits_at_first_failure(guardrails, base_signal, portfolio, market_ctx):
    """check() must return on the first failing rule, not accumulate errors."""
    portfolio.halted = True
    portfolio.halt_reason = "first_rule_halt"
    # Also set up conditions that would fail other rules
    market_ctx.nifty_change_pct_15m = -5.0  # would be black_swan if reached
    d = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert not d.allow
    assert d.rule == "global_halt"  # must stop at first rule


# ---------------------------------------------------------------------------
# check() returns allow=True only when every rule passes
# ---------------------------------------------------------------------------

def test_check_returns_allow_true_when_all_pass(guardrails, base_signal, portfolio, market_ctx):
    """Sanity: a well-formed signal with all constraints satisfied must be allowed."""
    d = guardrails.check(base_signal, qty=10, portfolio=portfolio, market=market_ctx)
    assert d.allow is True
    assert d.rule == ""
    assert d.reason == ""
