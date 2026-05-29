"""Main agent loop. Wires data feed -> aggregator -> regime -> strategies -> guardrails -> broker.

Flow per minute candle close:
  1. Update Nifty/VIX context, recompute regime
  2. For each subscribed symbol with a candle close:
       a. Run every strategy that supports the current regime
       b. For each emitted signal: size, run guardrails, place paper order if allowed
  3. Push live LTPs into the broker so stop-loss/target auto-exits can fire
  4. Snapshot equity
  5. At intraday_squareoff_at: force-close all open positions
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

import pandas as pd

from core.broker.base import IBroker
from core.broker.paper import PaperBroker
from core.config import Settings
from core.data.aggregator import CandleAggregator
from core.data.feed_base import ILiveFeed
from core.data.historical import fetch_daily
from core.data.universe import resolve_universe
from core.logging_setup import JsonlEventLogger
from core.persistence.store import Store
from core.regime.classifier import RegimeClassifier, RegimeSnapshot
from core.runtime_snapshot import RuntimeSnapshot, now_iso, write as write_snapshot
from core.risk.guardrails import (
    Guardrails,
    GuardrailDecision,
    MarketContext,
    PortfolioState,
)
from core.risk.sizing import position_size
from core.strategies.base import IStrategy
from core.strategies.mean_reversion import MeanReversion
from core.strategies.supertrend_short import SupertrendShort
from core.strategies.trend_breakout import TrendBreakout
from core.strategies.volatility_compression import VolatilityCompression
from core.types import Candle, OrderType, Regime, Side, Tick

log = logging.getLogger("agent.orchestrator")


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        feed: ILiveFeed,
        broker: IBroker,
        store: Store,
        events: JsonlEventLogger,
    ):
        self.s = settings
        self.feed = feed
        self.broker = broker
        self.store = store
        self.events = events

        self.symbols = resolve_universe(settings.universe.source, settings.universe.symbols)
        self.aggregator = CandleAggregator(intervals=["1m", "5m", "15m"])
        self.regime_classifier = RegimeClassifier(settings.regime)
        self.guardrails = Guardrails(settings.risk, settings.market, settings.execution)
        self.strategies: list[IStrategy] = self._build_strategies(settings)

        # Mutable runtime state
        self.current_regime = RegimeSnapshot(Regime.UNKNOWN, 0.0, 0.0, 0.0, "init")
        self.nifty_ohlc_daily: Optional[pd.DataFrame] = None
        self.adv_by_symbol: dict[str, int] = {}
        self.last_tick_age_seconds: float = float("inf")
        self.starting_equity_today = broker.equity()
        self.peak_equity = broker.equity()
        self.last_exit_by_symbol: dict[str, datetime] = {}
        self.spread_pct_by_symbol: dict[str, float] = {}
        self.nifty_ltp = 0.0
        self.nifty_change_pct_15m = 0.0
        self.vix = 0.0
        self.vix_change_pct_15m = 0.0
        self.halted = False
        self.halt_reason = ""

    # ---------- public API ----------

    def warmup(self) -> None:
        """Pre-load daily history for regime classification and per-symbol candles."""
        log.info("Warming up: fetching daily history for regime + symbols ...")
        nifty = fetch_daily("^NSEI", days=200) if False else None  # yf symbol differs; orchestrator-level shortcut
        if nifty is None:
            # Use Nifty 50 ETF as fallback proxy if direct index fetch fails
            nifty = fetch_daily("NIFTYBEES", days=200)
        self.nifty_ohlc_daily = nifty

        for sym in self.symbols:
            df = fetch_daily(sym, days=60)
            if df is None or df.empty:
                continue
            self.adv_by_symbol[sym] = int(df["volume"].tail(20).mean())

        if self.nifty_ohlc_daily is not None and not self.nifty_ohlc_daily.empty:
            self.current_regime = self.regime_classifier.classify(self.nifty_ohlc_daily, vix=15.0)
            log.info("Initial regime: %s — %s", self.current_regime.regime, self.current_regime.rationale)

    def start(self) -> None:
        self.feed.on_tick(self._on_tick)
        self.aggregator.on_candle(self._on_candle_close)
        self.feed.connect()
        self.feed.subscribe(self.symbols)
        log.info("Orchestrator running. Universe size: %d", len(self.symbols))

    def tick_lifecycle(self) -> None:
        """Periodic housekeeping — call from a 1-second timer in run loop."""
        self.last_tick_age_seconds = self.feed.last_tick_age_seconds()
        equity = self.broker.equity()
        self.peak_equity = max(self.peak_equity, equity)
        self.store.record_equity(
            cash=self.broker.cash(),
            equity=equity,
            realized_pnl=getattr(self.broker, "realized_pnl", 0.0),
            open_positions=len(self.broker.get_positions()),
        )
        self._maybe_squareoff_eod()
        self._check_global_halts()
        self._publish_snapshot(equity)

    def _publish_snapshot(self, equity: float) -> None:
        import os

        positions_dump = [
            {
                "symbol": p.symbol,
                "qty": p.qty,
                "avg_price": p.avg_price,
                "last_price": p.last_price,
                "stop_loss": p.stop_loss,
                "target": p.target,
                "unrealized_pnl": p.unrealized_pnl,
                "strategy": p.strategy,
                "opened_at": p.opened_at.isoformat() if p.opened_at else None,
            }
            for p in self.broker.get_positions()
        ]
        snap = RuntimeSnapshot(
            ts=now_iso(),
            pid=os.getpid(),
            mode=self.s.mode,
            feed_connected=self.feed.is_connected(),
            last_tick_age_seconds=self.last_tick_age_seconds,
            halted=self.halted,
            halt_reason=self.halt_reason,
            cash=self.broker.cash(),
            equity=equity,
            realized_pnl=getattr(self.broker, "realized_pnl", 0.0),
            starting_equity_today=self.starting_equity_today,
            peak_equity=self.peak_equity,
            open_positions=positions_dump,
            current_regime=self.current_regime.regime.value,
            regime_rationale=self.current_regime.rationale,
            nifty_ltp=self.nifty_ltp,
            vix=self.vix,
            universe_size=len(self.symbols),
            strategies_enabled=[s.name for s in self.strategies],
            config_path="config/default.yaml",
        )
        try:
            write_snapshot(snap, "data/snapshot.json")
        except OSError as e:
            log.warning("Snapshot write failed: %s", e)

    # ---------- handlers ----------

    def _on_tick(self, tick: Tick) -> None:
        self.aggregator.ingest(tick)
        self.broker.update_market_prices({tick.symbol: tick.ltp})
        if tick.bid > 0 and tick.ask > 0 and tick.ltp > 0:
            self.spread_pct_by_symbol[tick.symbol] = (tick.ask - tick.bid) / tick.ltp * 100.0

    def _on_candle_close(self, candle: Candle) -> None:
        if candle.interval != "5m":
            return  # decisions on 5-min closes; tune via config later
        history = self.aggregator.history(candle.symbol, "5m")
        if len(history) < 30:
            return
        df = pd.DataFrame(
            [
                {"open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume}
                for c in history
            ]
        )

        nifty_allow_trend, nifty_allow_range, nifty_allow_any = self._nifty_market_filter()
        for strat in self.strategies:
            if not strat.supports(self.current_regime.regime):
                continue
            sig = strat.evaluate(candle.symbol, df, self.current_regime.regime)
            if sig is None:
                continue
            if sig.side == Side.BUY:
                blocked = (
                    (not nifty_allow_any)
                    or (self.current_regime.regime == Regime.TREND and not nifty_allow_trend)
                    or (self.current_regime.regime in (Regime.RANGE, Regime.VOLATILE) and not nifty_allow_range)
                )
                if blocked:
                    log.debug("Nifty market filter blocked BUY for %s (%s)", candle.symbol, strat.name)
                    continue
            self._handle_signal(sig)

    def _nifty_market_filter(self) -> tuple[bool, bool, bool]:
        """Return (allow_trend_buys, allow_range_buys, allow_any_buys) based on Nifty vs 50/200-DMA.

        allow_any_buys=False when Nifty is below 200-DMA (structural decline).
        allow_range_buys=False when 50-DMA is falling (correction; RANGE longs also suppressed).
        allow_trend_buys=False when Nifty is below or 50-DMA is falling.
        """
        df = self.nifty_ohlc_daily
        if df is None or len(df) < 55:
            return True, True, True  # not enough history -- don't block
        close = df["close"]
        dma50  = close.rolling(50).mean()
        dma200 = close.rolling(200).mean()
        above_50  = close.iloc[-1] > dma50.iloc[-1]
        rising_50 = dma50.iloc[-1] > dma50.iloc[-5]
        above_200 = bool(close.iloc[-1] > dma200.iloc[-1]) if len(dma200.dropna()) >= 1 else True
        allow_trend = above_200 and above_50 and rising_50
        allow_range = above_200 and rising_50
        allow_any   = above_200
        return bool(allow_trend), bool(allow_range), bool(allow_any)

    def _handle_signal(self, sig) -> None:
        equity = self.broker.equity()
        qty = position_size(
            equity=equity,
            per_trade_risk_pct=self.s.risk.per_trade_risk_pct,
            signal=sig,
            max_position_pct=self.s.risk.max_position_pct,
        )
        market_ctx = MarketContext(
            now=datetime.now(),
            nifty_ltp=self.nifty_ltp,
            nifty_change_pct_15m=self.nifty_change_pct_15m,
            vix=self.vix,
            vix_change_pct_15m=self.vix_change_pct_15m,
            last_tick_age_seconds=self.last_tick_age_seconds,
            avg_daily_volumes=self.adv_by_symbol,
            spread_pct_by_symbol=self.spread_pct_by_symbol,
        )
        portfolio = PortfolioState(
            equity=equity,
            starting_equity_today=self.starting_equity_today,
            peak_equity=self.peak_equity,
            open_positions=self.broker.get_positions(),
            realized_pnl_today=getattr(self.broker, "realized_pnl", 0.0),
            last_exit_by_symbol=self.last_exit_by_symbol,
            halted=self.halted,
            halt_reason=self.halt_reason,
        )
        decision: GuardrailDecision = self.guardrails.check(sig, qty, portfolio, market_ctx)

        self.store.record_signal(
            ts=sig.ts.isoformat(),
            symbol=sig.symbol,
            side=sig.side.value,
            strategy=sig.strategy,
            regime=sig.regime.value,
            entry_price=sig.entry_price,
            stop_loss=sig.stop_loss,
            target=sig.target,
            confidence=sig.confidence,
            rationale=sig.rationale,
            accepted=decision.allow,
            rejection_reason=None if decision.allow else f"{decision.rule}: {decision.reason}",
        )
        self.events.emit(
            "signal",
            symbol=sig.symbol,
            strategy=sig.strategy,
            regime=sig.regime.value,
            qty=qty,
            allow=decision.allow,
            rule=decision.rule,
            reason=decision.reason,
        )

        if not decision.allow:
            self.store.record_guardrail(rule=decision.rule, symbol=sig.symbol, detail=decision.reason)
            log.info("Signal rejected: %s %s — %s: %s", sig.symbol, sig.strategy, decision.rule, decision.reason)
            return

        order = self.broker.place_order(
            symbol=sig.symbol,
            side=sig.side,
            qty=qty,
            order_type=OrderType.MARKET,
            stop_loss=sig.stop_loss,
            target=sig.target,
            strategy=sig.strategy,
        )
        log.info("Order %s for %s qty=%d -> %s", order.id, sig.symbol, qty, order.status.value)
        self.events.emit(
            "order",
            id=order.id,
            symbol=order.symbol,
            side=order.side.value,
            qty=order.filled_qty,
            price=order.filled_price,
            status=order.status.value,
            rejection=order.rejection_reason,
        )

    # ---------- helpers ----------

    def _build_strategies(self, settings: Settings) -> list[IStrategy]:
        out: list[IStrategy] = []
        scfg = settings.strategies
        if scfg.get("trend_breakout", {}).get("enabled", False):
            cfg = scfg["trend_breakout"]
            out.append(TrendBreakout(
                donchian_period=cfg.get("donchian_period", 20),
                atr_period=cfg.get("atr_period", 14),
                atr_stop_multiplier=cfg.get("atr_stop_multiplier", 2.0),
                target_r_multiple=cfg.get("target_r_multiple", 2.5),
            ))
        if scfg.get("mean_reversion", {}).get("enabled", False):
            cfg = scfg["mean_reversion"]
            out.append(MeanReversion(
                rsi_period=cfg.get("rsi_period", 14),
                rsi_oversold=cfg.get("rsi_oversold", 30),
                rsi_overbought=cfg.get("rsi_overbought", 70),
                bb_period=cfg.get("bb_period", 20),
                bb_std=cfg.get("bb_std", 2.0),
            ))
        if scfg.get("volatility_compression", {}).get("enabled", False):
            cfg = scfg["volatility_compression"]
            out.append(VolatilityCompression(nr_lookback=cfg.get("nr_lookback", 7)))
        if scfg.get("supertrend_short", {}).get("enabled", False):
            cfg = scfg["supertrend_short"]
            out.append(SupertrendShort(
                atr_period=cfg.get("atr_period", 10),
                multiplier=cfg.get("multiplier", 3.0),
                target_r_multiple=cfg.get("target_r_multiple", 2.0),
                stock_dma_period=cfg.get("stock_dma_period", 50),
            ))
        return out

    def _maybe_squareoff_eod(self) -> None:
        from core.risk.guardrails import _parse_t

        squareoff_t = _parse_t(self.s.market.intraday_squareoff_at)
        if datetime.now().time() < squareoff_t:
            return
        positions = self.broker.get_positions()
        if not positions:
            return
        log.warning("EOD square-off: closing %d positions", len(positions))
        for pos in positions:
            isinstance(self.broker, PaperBroker) and self.broker._auto_exit(  # type: ignore[attr-defined]
                pos, pos.last_price or pos.avg_price, "eod_squareoff"
            )

    def _check_global_halts(self) -> None:
        if self.halted:
            return
        equity = self.broker.equity()
        if self.starting_equity_today > 0:
            day_pnl_pct = (equity - self.starting_equity_today) / self.starting_equity_today * 100.0
            if day_pnl_pct < -self.s.risk.daily_loss_circuit_pct:
                self.halted = True
                self.halt_reason = f"daily loss circuit hit ({day_pnl_pct:.2f}%)"
                log.error("HALT: %s", self.halt_reason)
        if self.peak_equity > 0:
            dd_pct = (self.peak_equity - equity) / self.peak_equity * 100.0
            if dd_pct > self.s.risk.drawdown_circuit_pct:
                self.halted = True
                self.halt_reason = f"drawdown circuit hit ({dd_pct:.2f}%)"
                log.error("HALT: %s", self.halt_reason)
