"""Event-driven daily-bar backtest engine.

Crucially, this engine reuses the PRODUCTION guardrails, sizing, and strategies — no
parallel implementation. The only thing swapped is the live feed (replaced with
historical bars) and the broker (still PaperBroker, fed simulated prices).

Bar processing model (deliberately conservative):
  For each trading day D in [start, end]:
    1. Build candle history up to and including bar D-1's close (no look-ahead)
    2. Re-classify regime using nifty history up to D-1
    3. For each symbol, run every supported strategy on history-up-to-D-1
    4. If a signal fires, size it and run guardrails using bar D-1's close
       (signals would be generated at end of day D-1; orders fill on D's open)
    5. Submit accepted orders to PaperBroker — fills at D's open + slippage
    6. Within bar D: check stop-loss before target (pessimistic).
       If both hit by H/L of bar D, assume stop hit (worst case).
    7. Mark equity at D's close. Snapshot for equity curve.

This is a daily-bar simulator. Intraday refinements (fills mid-bar, partial fills)
are deliberately omitted — they add false precision without changing the gate decision.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional

import pandas as pd

from core.broker.paper import PaperBroker, TradeRecord
from core.config import ExecutionCfg, MarketCfg, RegimeCfg, RiskCfg, Settings
from core.regime.classifier import RegimeClassifier
from core.risk.guardrails import (
    Guardrails,
    GuardrailDecision,
    MarketContext,
    PortfolioState,
)
from core.risk.sizing import position_size
from core.strategies.base import IStrategy
from core.strategies.bb_squeeze import BbSqueeze
from core.strategies.ema_crossover import EmaCrossover
from core.strategies.mean_reversion import MeanReversion
from core.strategies.obv_trend import ObvTrend
from core.strategies.rsi_momentum import RsiMomentum
from core.strategies.supertrend import Supertrend
from core.strategies.trend_breakout import TrendBreakout
from core.strategies.volatility_compression import VolatilityCompression
from core.types import OrderType, Position, Regime, Side, Signal

log = logging.getLogger("backtest.engine")


@dataclass
class BacktestResult:
    starting_equity: float
    ending_equity: float
    trades: list[TradeRecord]
    equity_curve: pd.Series          # indexed by date, values = equity at close
    signal_count: int
    accepted_count: int
    rejected_count: int
    rejection_breakdown: dict[str, int]
    period_start: datetime
    period_end: datetime
    regime_distribution: dict[str, int] = None          # days per regime
    signal_count_by_strategy: dict[str, int] = None     # signals per strategy
    accepted_count_by_strategy: dict[str, int] = None   # accepted per strategy
    qty_zero_count: int = 0                             # signals killed by sizing before guardrails
    signal_count_by_symbol: dict[str, int] = None       # top signal-generating symbols

    def __post_init__(self):
        if self.regime_distribution is None:
            self.regime_distribution = {}
        if self.signal_count_by_strategy is None:
            self.signal_count_by_strategy = {}
        if self.accepted_count_by_strategy is None:
            self.accepted_count_by_strategy = {}
        if self.signal_count_by_symbol is None:
            self.signal_count_by_symbol = {}


class BacktestEngine:
    """Daily-bar event-driven backtest reusing Phase 1 production components."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.guardrails = Guardrails(settings.risk, settings.market, settings.execution)
        self.regime_classifier = RegimeClassifier(settings.regime)
        self.strategies: list[IStrategy] = self._build_strategies(settings)

    # ---------- public API ----------

    def run(
        self,
        symbol_history: dict[str, pd.DataFrame],
        nifty_history: pd.DataFrame,
        starting_equity: float,
        start_date: datetime,
        end_date: datetime,
    ) -> BacktestResult:
        broker = PaperBroker(starting_equity, self.settings.execution)
        broker.update_market_prices(self._initial_prices(symbol_history, start_date))

        common_dates = self._intersect_dates(symbol_history, nifty_history, start_date, end_date)
        if len(common_dates) < 30:
            raise ValueError(
                f"Not enough overlapping trading days for backtest "
                f"(got {len(common_dates)}, need >= 30)"
            )

        adv_by_symbol = self._compute_adv(symbol_history)
        equity_records: list[tuple[datetime, float]] = []
        signal_count = accepted = rejected = qty_zero_count = 0
        rejection_breakdown: dict[str, int] = {}
        regime_distribution: dict[str, int] = {}
        signal_count_by_strategy: dict[str, int] = {}
        accepted_count_by_strategy: dict[str, int] = {}
        signal_count_by_symbol: dict[str, int] = {}
        last_exit_by_symbol: dict[str, datetime] = {}
        peak_equity = starting_equity
        starting_equity_today = starting_equity
        last_date: Optional[datetime] = None

        for i, date in enumerate(common_dates):
            if i == 0:
                # Need at least one day of history before we can decide anything.
                equity_records.append((date, broker.equity()))
                last_date = date
                continue

            yday = common_dates[i - 1]

            # Reset day-bookkeeping on new calendar day.
            if last_date is None or date.date() != last_date.date():
                starting_equity_today = broker.equity()

            # Advance broker's simulated clock so cooldown and opened_at use backtest time.
            broker.sim_time = datetime.combine(date.date(), time(15, 30))

            # 1. Auto-exit existing positions intraday using bar D's H/L (stop checked first).
            self._process_intraday_exits(broker, symbol_history, date)

            # 2. Re-classify regime using Nifty up to yesterday.
            nifty_slice = nifty_history.loc[:yday]
            vix_proxy = self._estimate_vix(nifty_slice)
            regime = self.regime_classifier.classify(nifty_slice, vix=vix_proxy).regime

            regime_distribution[regime.value] = regime_distribution.get(regime.value, 0) + 1
            if regime == Regime.UNKNOWN:
                equity_records.append((date, broker.equity()))
                last_date = date
                continue

            # 3-5. Generate signals on yesterday's data and submit orders to fill on today's open.

            # Nifty trend filter: only allow BUY signals when Nifty is above its 50-DMA
            # and that DMA is rising. Blocks entries during broad market corrections.
            nifty_close = nifty_slice["close"]
            nifty_dma50 = nifty_close.rolling(50).mean()
            if len(nifty_dma50.dropna()) >= 5:
                nifty_above_dma = nifty_close.iloc[-1] > nifty_dma50.iloc[-1]
                nifty_dma_rising = nifty_dma50.iloc[-1] > nifty_dma50.iloc[-5]
            else:
                nifty_above_dma = True   # not enough history — don't block
                nifty_dma_rising = True
            nifty_buy_ok = nifty_above_dma and nifty_dma_rising

            for symbol, df in symbol_history.items():
                history = df.loc[:yday]
                if len(history) < 30:
                    continue
                today_row = df.loc[date] if date in df.index else None
                if today_row is None:
                    continue

                for strat in self.strategies:
                    if not strat.supports(regime):
                        continue
                    sig: Optional[Signal] = strat.evaluate(symbol, history, regime)
                    if sig is None:
                        continue
                    signal_count += 1
                    signal_count_by_strategy[strat.name] = signal_count_by_strategy.get(strat.name, 0) + 1
                    signal_count_by_symbol[symbol] = signal_count_by_symbol.get(symbol, 0) + 1

                    # Block long entries in TREND regime when Nifty is below/falling 50-DMA.
                    # RANGE/VOLATILE strategies are regime-specific and don't need this filter.
                    if sig.side == Side.BUY and regime == Regime.TREND and not nifty_buy_ok:
                        rejected += 1
                        rejection_breakdown["nifty_trend_filter"] = (
                            rejection_breakdown.get("nifty_trend_filter", 0) + 1
                        )
                        continue

                    # Override the signal's entry price to today's open (realistic fill timing).
                    sig.entry_price = float(today_row["open"])
                    if sig.side == Side.BUY and sig.stop_loss >= sig.entry_price:
                        rejected += 1
                        rejection_breakdown["stop_above_open_after_gap"] = (
                            rejection_breakdown.get("stop_above_open_after_gap", 0) + 1
                        )
                        continue

                    qty = position_size(
                        equity=broker.equity(),
                        per_trade_risk_pct=self.settings.risk.per_trade_risk_pct,
                        signal=sig,
                        max_position_pct=self.settings.risk.max_position_pct,
                    )
                    if qty == 0:
                        qty_zero_count += 1
                        rejected += 1
                        rejection_breakdown["qty_zero_sizing"] = rejection_breakdown.get("qty_zero_sizing", 0) + 1
                        continue

                    decision = self._check_guardrails(
                        sig, qty, broker, adv_by_symbol, last_exit_by_symbol,
                        starting_equity_today, peak_equity, date,
                    )
                    if not decision.allow:
                        rejected += 1
                        key = decision.rule
                        rejection_breakdown[key] = rejection_breakdown.get(key, 0) + 1
                        continue

                    broker.update_market_prices({sig.symbol: sig.entry_price})
                    order = broker.place_order(
                        symbol=sig.symbol, side=sig.side, qty=qty,
                        order_type=OrderType.MARKET,
                        stop_loss=sig.stop_loss, target=sig.target,
                        strategy=sig.strategy,
                    )
                    if order.status.value == "FILLED":
                        accepted += 1
                        accepted_count_by_strategy[strat.name] = accepted_count_by_strategy.get(strat.name, 0) + 1
                    else:
                        rejected += 1
                        rejection_breakdown[f"broker:{order.rejection_reason}"] = (
                            rejection_breakdown.get(f"broker:{order.rejection_reason}", 0) + 1
                        )

            # 6. Mark-to-market at bar D's close.
            close_prices = {
                s: float(df.loc[date, "close"]) for s, df in symbol_history.items()
                if date in df.index
            }
            broker.update_market_prices(close_prices)
            equity = broker.equity()
            peak_equity = max(peak_equity, equity)
            equity_records.append((date, equity))
            last_date = date

            # Track exits for cooldown tracking
            for trade in broker.trade_log[-10:]:  # cheap recent tail
                last_exit_by_symbol[trade.symbol] = trade.closed_at

        equity_series = pd.Series(
            [e for _, e in equity_records],
            index=pd.DatetimeIndex([d for d, _ in equity_records]),
            name="equity",
        )
        return BacktestResult(
            starting_equity=starting_equity,
            ending_equity=broker.equity(),
            trades=list(broker.trade_log),
            equity_curve=equity_series,
            signal_count=signal_count,
            accepted_count=accepted,
            rejected_count=rejected,
            rejection_breakdown=rejection_breakdown,
            period_start=common_dates[0],
            period_end=common_dates[-1],
            regime_distribution=regime_distribution,
            signal_count_by_strategy=signal_count_by_strategy,
            accepted_count_by_strategy=accepted_count_by_strategy,
            qty_zero_count=qty_zero_count,
            signal_count_by_symbol=signal_count_by_symbol,
        )

    # ---------- internals ----------

    def _build_strategies(self, settings: Settings) -> list[IStrategy]:
        out: list[IStrategy] = []
        scfg = settings.strategies

        if scfg.get("trend_breakout", {}).get("enabled", False):
            cfg = scfg["trend_breakout"]
            out.append(TrendBreakout(
                donchian_period=cfg.get("donchian_period", 15),
                atr_period=cfg.get("atr_period", 14),
                atr_stop_multiplier=cfg.get("atr_stop_multiplier", 1.5),
                target_r_multiple=cfg.get("target_r_multiple", 2.0),
            ))
        if scfg.get("mean_reversion", {}).get("enabled", False):
            cfg = scfg["mean_reversion"]
            out.append(MeanReversion(
                rsi_period=cfg.get("rsi_period", 14),
                rsi_oversold=cfg.get("rsi_oversold", 35),
                rsi_overbought=cfg.get("rsi_overbought", 65),
                bb_period=cfg.get("bb_period", 20),
                bb_std=cfg.get("bb_std", 2.0),
            ))
        if scfg.get("volatility_compression", {}).get("enabled", False):
            cfg = scfg["volatility_compression"]
            out.append(VolatilityCompression(nr_lookback=cfg.get("nr_lookback", 7)))
        if scfg.get("ema_crossover", {}).get("enabled", False):
            cfg = scfg["ema_crossover"]
            out.append(EmaCrossover(
                fast_period=cfg.get("fast_period", 9),
                slow_period=cfg.get("slow_period", 21),
                atr_period=cfg.get("atr_period", 14),
                atr_stop_multiplier=cfg.get("atr_stop_multiplier", 1.5),
                target_r_multiple=cfg.get("target_r_multiple", 2.0),
                volume_confirmation=cfg.get("volume_confirmation", True),
            ))
        if scfg.get("rsi_momentum", {}).get("enabled", False):
            cfg = scfg["rsi_momentum"]
            out.append(RsiMomentum(
                rsi_period=cfg.get("rsi_period", 14),
                rsi_pullback_low=cfg.get("rsi_pullback_low", 40.0),
                rsi_pullback_high=cfg.get("rsi_pullback_high", 55.0),
                trend_ema_period=cfg.get("trend_ema_period", 50),
                atr_period=cfg.get("atr_period", 14),
                atr_stop_multiplier=cfg.get("atr_stop_multiplier", 1.5),
                target_r_multiple=cfg.get("target_r_multiple", 2.0),
            ))
        if scfg.get("bb_squeeze", {}).get("enabled", False):
            cfg = scfg["bb_squeeze"]
            out.append(BbSqueeze(
                bb_period=cfg.get("bb_period", 20),
                bb_std=cfg.get("bb_std", 2.0),
                squeeze_lookback=cfg.get("squeeze_lookback", 20),
                atr_period=cfg.get("atr_period", 14),
                atr_stop_multiplier=cfg.get("atr_stop_multiplier", 1.5),
                target_r_multiple=cfg.get("target_r_multiple", 2.0),
            ))
        if scfg.get("supertrend", {}).get("enabled", False):
            cfg = scfg["supertrend"]
            out.append(Supertrend(
                atr_period=cfg.get("atr_period", 10),
                multiplier=cfg.get("multiplier", 3.0),
                target_r_multiple=cfg.get("target_r_multiple", 2.0),
            ))
        if scfg.get("obv_trend", {}).get("enabled", False):
            cfg = scfg["obv_trend"]
            out.append(ObvTrend(
                breakout_period=cfg.get("breakout_period", 15),
                atr_period=cfg.get("atr_period", 14),
                atr_stop_multiplier=cfg.get("atr_stop_multiplier", 1.5),
                target_r_multiple=cfg.get("target_r_multiple", 2.5),
            ))
        return out

    def _process_intraday_exits(
        self,
        broker: PaperBroker,
        symbol_history: dict[str, pd.DataFrame],
        date: datetime,
    ) -> None:
        """For each open position, evaluate today's H/L against stop and target.

        Conservative: if today's low <= stop, exit at stop (loss locked in).
        Only check target if stop wasn't hit. This mirrors how real markets
        often gap through levels in the worst direction first.
        """
        for pos in list(broker.get_positions()):
            df = symbol_history.get(pos.symbol)
            if df is None or date not in df.index:
                continue
            row = df.loc[date]
            low = float(row["low"])
            high = float(row["high"])
            if low <= pos.stop_loss:
                broker.update_market_prices({pos.symbol: pos.stop_loss})  # triggers auto-exit
            elif pos.target is not None and high >= pos.target:
                broker.update_market_prices({pos.symbol: pos.target})

    def _check_guardrails(
        self,
        signal: Signal,
        qty: int,
        broker: PaperBroker,
        adv_by_symbol: dict[str, int],
        last_exit_by_symbol: dict[str, datetime],
        starting_equity_today: float,
        peak_equity: float,
        date: datetime,
    ) -> GuardrailDecision:
        # Use a simulated time inside the trading window so the market_window check passes.
        sim_now = datetime.combine(date.date(), time(10, 30))
        ctx = MarketContext(
            now=sim_now,
            nifty_ltp=0.0,
            nifty_change_pct_15m=0.0,
            vix=15.0,
            vix_change_pct_15m=0.0,
            last_tick_age_seconds=0.0,
            avg_daily_volumes=adv_by_symbol,
            spread_pct_by_symbol={},  # backtest assumes spreads within tolerance
        )
        portfolio = PortfolioState(
            equity=broker.equity(),
            starting_equity_today=starting_equity_today,
            peak_equity=peak_equity,
            open_positions=broker.get_positions(),
            realized_pnl_today=0.0,
            last_exit_by_symbol=last_exit_by_symbol,
            halted=False,
        )
        return self.guardrails.check(signal, qty, portfolio, ctx)

    def _initial_prices(
        self, symbol_history: dict[str, pd.DataFrame], start_date: datetime
    ) -> dict[str, float]:
        out: dict[str, float] = {}
        for s, df in symbol_history.items():
            try:
                idx = df.index.searchsorted(start_date)
                if idx < len(df):
                    out[s] = float(df.iloc[idx]["close"])
            except Exception:
                continue
        return out

    @staticmethod
    def _intersect_dates(
        symbol_history: dict[str, pd.DataFrame],
        nifty_history: pd.DataFrame,
        start: datetime,
        end: datetime,
    ) -> list[pd.Timestamp]:
        dates = nifty_history.loc[start:end].index
        # Use Nifty's calendar as the master calendar — symbols with missing days are skipped.
        return [d for d in dates]

    @staticmethod
    def _compute_adv(symbol_history: dict[str, pd.DataFrame]) -> dict[str, int]:
        return {
            s: int(df["volume"].tail(20).mean()) if len(df) >= 20 else int(df["volume"].mean() or 0)
            for s, df in symbol_history.items()
        }

    @staticmethod
    def _estimate_vix(nifty: pd.DataFrame) -> float:
        """Quick VIX proxy from Nifty realised vol when we don't have actual VIX history.

        20-day annualised stdev of daily returns. Crude but consistent — production runs
        feed actual VIX from the live feed.
        """
        if len(nifty) < 20:
            return 15.0
        rets = nifty["close"].pct_change().dropna().tail(20)
        if rets.empty:
            return 15.0
        return float(rets.std() * (252 ** 0.5) * 100.0)
