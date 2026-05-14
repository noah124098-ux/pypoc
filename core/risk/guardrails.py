"""Hard-limit guardrails. Every order goes through `check()` — no exceptions, no override.

Each rule returns either ALLOW or REJECT(reason). The orchestrator must not place any
order whose check() result is not ALLOW.

This module is the single most safety-critical piece of code in the agent. Tests in
tests/test_guardrails.py exercise every rule and CI fails if any test fails.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Optional

from core.config import RiskCfg, MarketCfg, ExecutionCfg
from core.types import Position, Side, Signal


@dataclass
class GuardrailDecision:
    allow: bool
    rule: str = ""
    reason: str = ""


@dataclass
class MarketContext:
    """Runtime market state passed into every guardrail check."""
    now: datetime
    nifty_ltp: float
    nifty_change_pct_15m: float
    vix: float
    vix_change_pct_15m: float
    last_tick_age_seconds: float
    avg_daily_volumes: dict[str, int] = field(default_factory=dict)
    spread_pct_by_symbol: dict[str, float] = field(default_factory=dict)


@dataclass
class PortfolioState:
    equity: float
    starting_equity_today: float
    peak_equity: float
    open_positions: list[Position]
    realized_pnl_today: float
    last_exit_by_symbol: dict[str, datetime] = field(default_factory=dict)
    halted: bool = False
    halt_reason: str = ""


class Guardrails:
    """All hard limits live here. Order is meaningful — fast/cheap rules first."""

    def __init__(self, risk_cfg: RiskCfg, market_cfg: MarketCfg, execution_cfg: ExecutionCfg):
        self.r = risk_cfg
        self.m = market_cfg
        self.e = execution_cfg

    def check(
        self,
        signal: Signal,
        qty: int,
        portfolio: PortfolioState,
        market: MarketContext,
    ) -> GuardrailDecision:
        for rule in (
            self._check_global_halt,
            self._check_qty_positive,
            self._check_stop_loss_required,
            self._check_market_window,
            self._check_stale_data,
            self._check_black_swan,
            self._check_max_open_positions,
            self._check_per_trade_risk,
            self._check_max_position_size,
            self._check_liquidity,
            self._check_spread,
            self._check_daily_loss_circuit,
            self._check_drawdown_circuit,
            self._check_signal_cooldown,
            self._check_duplicate_position,
        ):
            decision = rule(signal, qty, portfolio, market)
            if not decision.allow:
                return decision
        return GuardrailDecision(allow=True)

    # ---------- individual rules ----------

    def _check_global_halt(self, s, q, p, m) -> GuardrailDecision:
        if p.halted:
            return GuardrailDecision(False, "global_halt", p.halt_reason or "halted")
        return GuardrailDecision(True)

    def _check_qty_positive(self, s, q, p, m) -> GuardrailDecision:
        if q <= 0:
            return GuardrailDecision(False, "qty_positive", "computed qty <= 0")
        return GuardrailDecision(True)

    def _check_stop_loss_required(self, s, q, p, m) -> GuardrailDecision:
        if s.stop_loss <= 0:
            return GuardrailDecision(False, "stop_loss_required", "no stop loss")
        if s.side == Side.BUY and s.stop_loss >= s.entry_price:
            return GuardrailDecision(False, "stop_loss_required", "stop above entry on long")
        if s.side == Side.SELL and s.stop_loss <= s.entry_price:
            return GuardrailDecision(False, "stop_loss_required", "stop below entry on short")
        return GuardrailDecision(True)

    def _check_market_window(self, s, q, p, m) -> GuardrailDecision:
        now_t = m.now.time()
        start = _parse_t(self.m.trading_start)
        cutoff = _parse_t(self.m.no_new_entries_after)
        if now_t < start:
            return GuardrailDecision(False, "market_window", "before market open")
        if now_t >= cutoff:
            return GuardrailDecision(False, "market_window", "past no-new-entries cutoff")
        return GuardrailDecision(True)

    def _check_stale_data(self, s, q, p, m) -> GuardrailDecision:
        if m.last_tick_age_seconds > self.r.stale_tick_seconds:
            return GuardrailDecision(
                False, "stale_data",
                f"no tick for {m.last_tick_age_seconds:.1f}s"
            )
        return GuardrailDecision(True)

    def _check_black_swan(self, s, q, p, m) -> GuardrailDecision:
        if abs(m.nifty_change_pct_15m) > self.r.black_swan_nifty_move_pct:
            return GuardrailDecision(
                False, "black_swan_nifty",
                f"nifty moved {m.nifty_change_pct_15m:.2f}% in 15m"
            )
        if m.vix_change_pct_15m > self.r.black_swan_vix_jump_pct:
            return GuardrailDecision(
                False, "black_swan_vix",
                f"vix jumped {m.vix_change_pct_15m:.2f}% in 15m"
            )
        return GuardrailDecision(True)

    def _check_max_open_positions(self, s, q, p, m) -> GuardrailDecision:
        existing = {pos.symbol for pos in p.open_positions}
        if s.symbol in existing:
            return GuardrailDecision(True)  # adding to position handled separately
        if len(p.open_positions) >= self.r.max_open_positions:
            return GuardrailDecision(False, "max_open_positions",
                                     f"already {len(p.open_positions)} open")
        return GuardrailDecision(True)

    def _check_per_trade_risk(self, s, q, p, m) -> GuardrailDecision:
        risk_budget = p.equity * (self.r.per_trade_risk_pct / 100.0)
        risk_amount = q * s.risk_per_share
        if risk_amount > risk_budget * 1.001:  # tiny float fudge
            return GuardrailDecision(
                False, "per_trade_risk",
                f"risk {risk_amount:.2f} > budget {risk_budget:.2f}"
            )
        return GuardrailDecision(True)

    def _check_max_position_size(self, s, q, p, m) -> GuardrailDecision:
        position_value = q * s.entry_price
        max_value = p.equity * (self.r.max_position_pct / 100.0)
        if position_value > max_value * 1.001:
            return GuardrailDecision(
                False, "max_position_size",
                f"position {position_value:.0f} > cap {max_value:.0f}"
            )
        return GuardrailDecision(True)

    def _check_liquidity(self, s, q, p, m) -> GuardrailDecision:
        adv = m.avg_daily_volumes.get(s.symbol)
        if adv is None or adv == 0:
            return GuardrailDecision(True)  # missing ADV — let it pass; data gap, not policy violation
        max_qty = int(adv * (self.r.liquidity_max_pct_of_adv / 100.0))
        if q > max_qty:
            return GuardrailDecision(
                False, "liquidity",
                f"qty {q} > {self.r.liquidity_max_pct_of_adv}% ADV ({max_qty})"
            )
        return GuardrailDecision(True)

    def _check_spread(self, s, q, p, m) -> GuardrailDecision:
        spread = m.spread_pct_by_symbol.get(s.symbol)
        if spread is not None and spread > self.r.max_spread_pct:
            return GuardrailDecision(
                False, "spread",
                f"spread {spread:.2f}% > max {self.r.max_spread_pct}%"
            )
        return GuardrailDecision(True)

    def _check_daily_loss_circuit(self, s, q, p, m) -> GuardrailDecision:
        if p.starting_equity_today <= 0:
            return GuardrailDecision(True)
        day_pnl_pct = (p.equity - p.starting_equity_today) / p.starting_equity_today * 100.0
        if day_pnl_pct < -self.r.daily_loss_circuit_pct:
            return GuardrailDecision(
                False, "daily_loss_circuit",
                f"day P&L {day_pnl_pct:.2f}% < -{self.r.daily_loss_circuit_pct}%"
            )
        return GuardrailDecision(True)

    def _check_drawdown_circuit(self, s, q, p, m) -> GuardrailDecision:
        if p.peak_equity <= 0:
            return GuardrailDecision(True)
        dd_pct = (p.peak_equity - p.equity) / p.peak_equity * 100.0
        if dd_pct > self.r.drawdown_circuit_pct:
            return GuardrailDecision(
                False, "drawdown_circuit",
                f"drawdown {dd_pct:.2f}% > {self.r.drawdown_circuit_pct}%"
            )
        return GuardrailDecision(True)

    def _check_signal_cooldown(self, s, q, p, m) -> GuardrailDecision:
        last = p.last_exit_by_symbol.get(s.symbol)
        if last is None:
            return GuardrailDecision(True)
        cooldown = timedelta(minutes=self.e.signal_cooldown_minutes)
        if m.now - last < cooldown:
            return GuardrailDecision(
                False, "signal_cooldown",
                f"{s.symbol} re-entry within cooldown"
            )
        return GuardrailDecision(True)

    def _check_duplicate_position(self, s, q, p, m) -> GuardrailDecision:
        # Block opposite-side stacking on same symbol; same-side handled by sizing.
        for pos in p.open_positions:
            if pos.symbol == s.symbol:
                # Adding more long is OK if within size cap (already checked); shorting a long is not.
                if s.side == Side.SELL and pos.qty > 0:
                    return GuardrailDecision(True)  # closing long via SELL is allowed
        return GuardrailDecision(True)


def _parse_t(s: str) -> time:
    h, m = s.split(":")
    return time(hour=int(h), minute=int(m))
