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
from core.config import Settings, reload_settings
from core.data.aggregator import CandleAggregator
from core.data.feed_base import ILiveFeed
from core.data.historical import fetch_daily
from core.data.universe import resolve_universe
from core.logging_setup import JsonlEventLogger, log_agent_halted, log_order_rejected, log_trade_filled
from core.persistence.store import Store
from core.regime.classifier import RegimeClassifier, RegimeSnapshot
from core.runtime_snapshot import RuntimeSnapshot, now_iso, write as write_snapshot
from core.risk.guardrails import (
    Guardrails,
    GuardrailDecision,
    MarketContext,
    PortfolioState,
)
from core.data.economic_calendar import is_blackout_day
from core.data.nse_pcr import get_nifty_pcr
from core.data.nse_vix import get_vix
from core.risk.sizing import position_size
from core.strategies.base import IStrategy
from core.strategies.mean_reversion import MeanReversion
from core.strategies.supertrend_short import SupertrendShort
from core.strategies.trend_breakout import TrendBreakout
from core.strategies.volatility_compression import VolatilityCompression
from core.types import Candle, OrderType, Regime, Side, Signal, Tick

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
        self.drawdown_warning = False
        self.daily_loss_warning = False
        self._last_vix_fetch: float = 0.0
        self._last_config_reload: float = 0.0
        # Intraday regime tick distribution — reset at EOD
        self._regime_ticks: dict[str, int] = {}

        try:
            from core.config import Secrets
            from core.notifications.telegram import TelegramNotifier
            _sec = Secrets.from_env()
            self.telegram = TelegramNotifier(_sec.telegram_bot_token, _sec.telegram_chat_id) if self.s.notifications.telegram_enabled else None
        except Exception:
            self.telegram = None

        try:
            from core.config import Secrets
            from core.notifications.email_notifier import EmailNotifier
            _sec2 = Secrets.from_env()
            self.email_notifier: Optional[EmailNotifier] = (
                EmailNotifier(
                    _sec2.smtp_host,
                    _sec2.smtp_port,
                    _sec2.smtp_user,
                    _sec2.smtp_password,
                    _sec2.email_from,
                    _sec2.email_to,
                )
                if self.s.notifications.email_enabled
                else None
            )
        except Exception:
            self.email_notifier = None

        # Wire exit callback so PaperBroker auto-exits fire Telegram alerts.
        if isinstance(self.broker, PaperBroker):
            self.broker.on_exit = self._on_position_exit

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
        self._process_command_queue()
        self.last_tick_age_seconds = self.feed.last_tick_age_seconds()
        equity = self.broker.equity()
        self.peak_equity = max(self.peak_equity, equity)

        # Refresh India VIX from NSE every 60 seconds.  get_vix() is fail-open:
        # returns None on network/parse errors, in which case we keep the last
        # known value so the regime classifier and guardrails are not disrupted.
        if time.time() - self._last_vix_fetch > 60:
            fresh_vix = get_vix()
            if fresh_vix is not None:
                self.vix = fresh_vix
            self._last_vix_fetch = time.time()

        # Reload only safe risk params from YAML every 60 seconds so the operator
        # can tweak per_trade_risk_pct / max_open_positions / circuit thresholds
        # without restarting the agent.  Universe, mode, and execution fields are
        # intentionally NOT reloaded — changing those at runtime is unsafe.
        if time.time() - self._last_config_reload > 60:
            try:
                config_path = self.s.config_path if hasattr(self.s, "config_path") else "config/default.yaml"
                new_settings = reload_settings(config_path)
                self.s.risk.per_trade_risk_pct = new_settings.risk.per_trade_risk_pct
                self.s.risk.max_open_positions = new_settings.risk.max_open_positions
                self.s.risk.daily_loss_circuit_pct = new_settings.risk.daily_loss_circuit_pct
                self.s.risk.drawdown_circuit_pct = new_settings.risk.drawdown_circuit_pct
                self.guardrails = Guardrails(self.s.risk, self.s.market, self.s.execution)
                log.debug("Config reloaded from disk")
            except Exception as e:
                log.warning("Config reload failed: %s", e)
            self._last_config_reload = time.time()

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
            drawdown_warning=self.drawdown_warning,
            daily_loss_warning=self.daily_loss_warning,
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
        # Track regime distribution for EOD daily_summary event
        regime_key = self.current_regime.regime.value
        self._regime_ticks[regime_key] = self._regime_ticks.get(regime_key, 0) + 1
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
                if is_blackout_day(datetime.now(), buffer_days=1):
                    log.debug("Economic blackout — skipping BUY signal for %s", candle.symbol)
                    continue
            self._handle_signal(sig)

    def _nifty_market_filter(self) -> tuple[bool, bool, bool]:
        """Return (allow_trend_buys, allow_range_buys, allow_any_buys).

        allow_any_buys=False when Nifty is below 200-DMA (structural decline).
        allow_range_buys=False when 50-DMA is falling (correction; RANGE longs also suppressed).
        allow_trend_buys=False when Nifty is below or 50-DMA is falling.

        PCR filter (live only):
          If the Nifty Put-Call Ratio < 0.7 (bearish options-market sentiment — calls
          dominant, distribution phase), allow_trend_buys is suppressed even when the
          DMA conditions are met.  Returns None when the NSE feed is unavailable
          (fail-open: does not block trading).

        FII/DII institutional sentiment filter:
          If the 3-day average FII net flow is strongly negative (< -500 crore),
          allow_trend_buys is suppressed — institutions are net sellers and it is
          unwise to fight that headwind with new TREND BUY entries.
          Fail-open: if the fetch returns None (network issue, outside market hours),
          we do not block trading.
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

        # PCR sentiment gate: suppress TREND BUYs when options market turns very bearish.
        # Fail-open: if the fetch returns None (network issue, outside market hours),
        # we do not block trading.
        pcr = get_nifty_pcr()
        if pcr is not None and pcr < 0.7:
            allow_trend = False

        # VIX 18-20 danger zone: suppress TREND BUYs when volatility is elevated.
        # In live mode self.vix is the real India VIX from NSE (refreshed every 60s).
        # 18 = elevated but not yet VOLATILE threshold (which is 20).
        # Applies to TREND BUYs only — RANGE strategies and shorts are not affected.
        # Fail-open: if vix == 0.0 (not yet fetched), do not block.
        if self.vix > 0 and self.vix >= 18.0:
            allow_trend = False

        # FII/DII institutional sentiment gate: suppress TREND BUYs when foreign
        # institutions are strong net sellers (avg FII net < -500 crore over 3 days).
        # Fail-open: if get_institutional_sentiment() returns None (fetch failed or
        # neutral/insufficient data), we do not block trading.
        try:
            from core.data.nse_fii_dii import get_institutional_sentiment
            _fii_sentiment = get_institutional_sentiment()
        except Exception:
            _fii_sentiment = None
        if _fii_sentiment == "BEARISH":
            allow_trend = False  # institutions are net sellers — don't fight them

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
            log_order_rejected(log, symbol=sig.symbol, reason=f"{decision.rule}: {decision.reason}")
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
        if order.status.value == "FILLED":
            log_trade_filled(
                log,
                symbol=order.symbol,
                side=order.side.value,
                qty=order.filled_qty,
                price=order.filled_price,
                strategy=sig.strategy,
                regime=sig.regime.value,
            )
        else:
            log_order_rejected(log, symbol=order.symbol, reason=order.rejection_reason or order.status.value)
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
        if order.status.value == "FILLED" and self.telegram and self.s.notifications.telegram_enabled:
            try:
                self.telegram.send_trade_alert(
                    symbol=order.symbol,
                    side=order.side.value,
                    strategy=sig.strategy,
                    pnl=0.0,
                    reason="entry",
                )
            except Exception as _e:
                log.warning("Telegram entry alert failed: %s", _e)

    def _on_position_exit(self, symbol: str, pnl: float, exit_reason: str, strategy: str) -> None:
        """Called by PaperBroker whenever a position is auto-closed (stop/target/EOD)."""
        if self.telegram and self.s.notifications.telegram_enabled:
            try:
                self.telegram.send_trade_alert(
                    symbol=symbol,
                    side="EXIT",
                    strategy=strategy,
                    pnl=pnl,
                    reason=exit_reason,
                )
            except Exception as _e:
                log.warning("Telegram exit alert failed: %s", _e)

    # ---------- helpers ----------

    # Allowlist of valid command types — unknown types are rejected immediately.
    _ALLOWED_COMMAND_TYPES: frozenset[str] = frozenset(
        ["halt_agent", "resume_agent", "update_risk_param", "place_paper_order", "reload_config"]
    )

    def _process_command_queue(self) -> None:
        from core.command_queue import read_pending, update_status
        try:
            for cmd in read_pending():
                update_status(cmd.id, "processing")
                try:
                    # Reject any command type not in the explicit allowlist.
                    if cmd.type not in self._ALLOWED_COMMAND_TYPES:
                        update_status(cmd.id, "rejected", f"unknown command type: {cmd.type}")
                        log.warning("Command queue: rejected unknown command type '%s'", cmd.type)
                        continue

                    if cmd.type == "halt_agent":
                        self.halted = True
                        # Cap halt reason at 200 chars to prevent memory/snapshot abuse.
                        reason = str(cmd.params.get("reason", "manual halt via MCP"))[:200]
                        self.halt_reason = reason
                        update_status(cmd.id, "done", f"halted: {self.halt_reason}")

                    elif cmd.type == "resume_agent":
                        # Only allow resume if circuits not triggered
                        equity = self.broker.equity()
                        day_pnl = (equity - self.starting_equity_today) / self.starting_equity_today * 100
                        if day_pnl < -self.s.risk.daily_loss_circuit_pct:
                            update_status(cmd.id, "rejected", "daily loss circuit still active")
                        else:
                            self.halted = False
                            self.halt_reason = ""
                            update_status(cmd.id, "done", "resumed")

                    elif cmd.type == "update_risk_param":
                        param = cmd.params["param"]
                        value = float(cmd.params["value"])
                        setattr(self.s.risk, param, value)
                        update_status(cmd.id, "done", f"set {param}={value}")

                    elif cmd.type == "place_paper_order":
                        side = Side.BUY if cmd.params["side"] == "BUY" else Side.SELL
                        ltp = self.broker._latest_prices.get(cmd.params["symbol"])
                        if ltp is None:
                            update_status(cmd.id, "rejected", "no market price")
                        else:
                            qty = int(cmd.params["qty"])
                            stop_loss = ltp * (0.98 if side == Side.BUY else 1.02)
                            target = ltp * (1.04 if side == Side.BUY else 0.96)
                            strategy_name = cmd.params.get("strategy", "manual")
                            # Build a minimal signal so it can pass through guardrails.
                            sig = Signal(
                                symbol=cmd.params["symbol"],
                                side=side,
                                entry_price=ltp,
                                stop_loss=stop_loss,
                                target=target,
                                strategy=strategy_name,
                                regime=self.current_regime.regime,
                                confidence=1.0,
                                rationale="manual MCP order",
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
                                equity=self.broker.equity(),
                                starting_equity_today=self.starting_equity_today,
                                peak_equity=self.peak_equity,
                                open_positions=self.broker.get_positions(),
                                realized_pnl_today=getattr(self.broker, "realized_pnl", 0.0),
                                last_exit_by_symbol=self.last_exit_by_symbol,
                                halted=self.halted,
                                halt_reason=self.halt_reason,
                            )
                            decision: GuardrailDecision = self.guardrails.check(sig, qty, portfolio, market_ctx)
                            if not decision.allow:
                                update_status(cmd.id, "rejected", f"guardrail blocked: {decision.rule}: {decision.reason}")
                            else:
                                order = self.broker.place_order(
                                    symbol=cmd.params["symbol"],
                                    side=side,
                                    qty=qty,
                                    order_type=OrderType.MARKET,
                                    stop_loss=stop_loss,
                                    target=target,
                                    strategy=strategy_name,
                                )
                                update_status(cmd.id, "done", f"order {order.id} {order.status.value}")

                    elif cmd.type == "reload_config":
                        try:
                            config_path = self.s.config_path if hasattr(self.s, "config_path") else "config/default.yaml"
                            new_settings = reload_settings(config_path)
                            self.s.risk.per_trade_risk_pct = new_settings.risk.per_trade_risk_pct
                            self.s.risk.max_open_positions = new_settings.risk.max_open_positions
                            self.s.risk.daily_loss_circuit_pct = new_settings.risk.daily_loss_circuit_pct
                            self.s.risk.drawdown_circuit_pct = new_settings.risk.drawdown_circuit_pct
                            self.guardrails = Guardrails(self.s.risk, self.s.market, self.s.execution)
                            self._last_config_reload = time.time()
                            log.info("Config reloaded via command queue")
                            update_status(cmd.id, "done", "config reloaded")
                        except Exception as e:
                            update_status(cmd.id, "rejected", f"reload failed: {e}")

                    else:
                        update_status(cmd.id, "rejected", f"unknown command type: {cmd.type}")
                except Exception as e:
                    update_status(cmd.id, "rejected", str(e))
        except Exception as e:
            log.warning("Command queue processing failed: %s", e)

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

        equity = self.broker.equity()
        pnl = getattr(self.broker, "realized_pnl", 0.0)
        trades_today = len(getattr(self.broker, "trade_log", []))
        day_pnl_pct = (
            (equity - self.starting_equity_today) / self.starting_equity_today * 100.0
            if self.starting_equity_today > 0 else 0.0
        )

        # Emit structured daily_summary event to events.jsonl
        self.events.emit_daily_summary(
            equity=equity,
            day_pnl=pnl,
            day_pnl_pct=round(day_pnl_pct, 4),
            trades_today=trades_today,
            regime_distribution=dict(self._regime_ticks),
        )
        # Reset intraday regime tick counter for the next trading day
        self._regime_ticks = {}

        if getattr(self, 'telegram', None) and self.s.notifications.telegram_enabled:
            self.telegram.send_daily_summary(equity, pnl, trades_today, self.current_regime.regime.value)

        if self.s.notifications.email_enabled and getattr(self, 'email_notifier', None):
            try:
                from core.analytics.performance_report import generate_html_report
                html = generate_html_report("data/agent.db", "data/snapshot.json")
                self.email_notifier.send_eod_report(
                    equity=equity,
                    pnl=pnl,
                    trades=trades_today,
                    review_summary=html,
                )
            except Exception as e:
                log.warning("EOD email failed: %s", e)

    def _check_global_halts(self) -> None:
        if self.halted:
            return
        equity = self.broker.equity()
        circuit_pct = self.s.risk.drawdown_circuit_pct
        daily_circuit_pct = self.s.risk.daily_loss_circuit_pct
        if self.starting_equity_today > 0:
            day_pnl_pct = (equity - self.starting_equity_today) / self.starting_equity_today * 100.0
            # Early daily-loss warning at half the circuit threshold (default -1.5% when circuit is -3%).
            daily_warn_pct = daily_circuit_pct / 2.0
            if day_pnl_pct < -daily_warn_pct and not self.daily_loss_warning:
                self.daily_loss_warning = True
                log.warning(
                    "Daily loss alert: %.1f%% of starting equity (circuit at -%.1f%%)",
                    abs(day_pnl_pct), daily_circuit_pct,
                )
                if getattr(self, 'telegram', None) and self.s.notifications.telegram_enabled:
                    try:
                        self.telegram.send(
                            f"⚠️ Daily Loss Warning: {abs(day_pnl_pct):.1f}%"
                        )
                    except Exception as _e:
                        log.warning("Telegram daily loss warning failed: %s", _e)
            elif day_pnl_pct >= -daily_warn_pct:
                # Reset warning if loss recovers above threshold
                self.daily_loss_warning = False
            if day_pnl_pct < -daily_circuit_pct:
                self.halted = True
                self.halt_reason = f"daily loss circuit hit ({day_pnl_pct:.2f}%)"
                self.daily_loss_warning = False
                log_agent_halted(log, reason="daily_loss_circuit", pct=day_pnl_pct)
                if getattr(self, 'telegram', None): self.telegram.send_halt_alert(self.halt_reason)
        if self.peak_equity > 0:
            dd_pct = (self.peak_equity - equity) / self.peak_equity * 100.0
            # Early drawdown warning at half the circuit threshold (default 5% when circuit is 10%).
            dd_warn_pct = circuit_pct / 2.0
            if dd_pct > dd_warn_pct and not self.drawdown_warning:
                self.drawdown_warning = True
                log.warning(
                    "Drawdown alert: %.1f%% of peak (circuit at %.1f%%)",
                    dd_pct, circuit_pct,
                )
                if getattr(self, 'telegram', None) and self.s.notifications.telegram_enabled:
                    try:
                        self.telegram.send(
                            f"⚠️ Drawdown Warning: {dd_pct:.1f}%"
                        )
                    except Exception as _e:
                        log.warning("Telegram drawdown warning failed: %s", _e)
            elif dd_pct <= dd_warn_pct:
                # Reset warning if drawdown recovers
                self.drawdown_warning = False
            if dd_pct > circuit_pct:
                self.halted = True
                self.halt_reason = f"drawdown circuit hit ({dd_pct:.2f}%)"
                self.drawdown_warning = False
                log_agent_halted(log, reason="drawdown_circuit", pct=-dd_pct)
                if getattr(self, 'telegram', None): self.telegram.send_halt_alert(self.halt_reason)
