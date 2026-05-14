---
name: project-nse-trading-agent
description: Design decisions locked for the NSE automated trading agent — multi-style regime-aware, Nifty 50, conservative risk, broker-agnostic, MCP-exposed, paper-trade only for v1
metadata:
  type: project
---

Locked design decisions (decided 2026-05-13 via Q&A):

- **Trading style:** Multi-style regime-aware. Agent classifies market regime (TREND / RANGE / VOLATILE) and switches between trend-breakout, mean-reversion, and volatility-compression strategies.
- **Universe:** Nifty 50 only for v1.
- **Risk model:** Conservative — 1-2% capital per trade, hard stop loss on every order, max 5 concurrent positions, daily loss circuit at -3%, drawdown circuit at -10%.
- **Decision engine:** Hybrid — deterministic rules execute trades intraday, Claude (Opus 4.7) runs an EOD review and proposes parameter adjustments for the next day.
- **Rollout:** Paper-trade only for v1. No live broker integration until backtest gate passes and 4-8 weeks of paper-trading prove stable.
- **Deployment:** Build portable. Originally local Python on Windows for dev; now also on a Windows Server EC2 for 24/7 use. See [[project-ec2-deployment]].
- **Broker:** Broker-agnostic IBroker interface. PaperBroker is the only implementation. Live broker (Upstox / Kite / a separate Angel One app) deferred to a future phase, with its own credentials.
- **Live data source:** Angel One SmartAPI (`core/data/angelone_feed.py` ticks, `core/data/angelone_history.py` historical OHLC). DATA-ONLY constraint applies — see [[feedback-angel-one-data-only]].
- **Backtest data fallback:** NSE Bhavcopy archives (`core/data/bhavcopy.py`), no creds needed. Used because user's network blocks yfinance with TLS errors and nseindia.com API has bot detection.
- **Market signals:** Technicals + index regime (Nifty/VIX/FII-DII) + news sentiment + options data (PCR/OI/max pain). Phase 1 covers technicals + regime; news/options/FII deferred to Phase 3.
- **Backtest gate (strict):** Walk-forward on 3+ years of Nifty 50 daily data. Thresholds: Sharpe > 1.2, MaxDD < 15%, win rate > 45%, profit factor > 1.5, ≥ 100 trades, gate file ≤ 30 days old. Live mode refuses to start unless gate passes — enforced in `cli.py cmd_run` via `backtest.gate.is_live_allowed()`.
- **Gate currently fails.** See [[project-gate-failure]].
- **MCP integration:** Yes — `mcp_server/` exposes 10 read-only tools (get_status, get_positions, get_pnl, get_recent_signals, get_recent_trades, get_guardrail_rejections, get_equity_curve, get_regime_history, get_universe, get_config_summary). Mutating tools deferred to Phase 6b via command-queue.
- **Notifications:** Telegram real-time alerts + EOD email report + local Streamlit dashboard. All optional, deferred to Phase 5.
- **Paper capital:** ₹1000 (user-specified; warning emitted because <₹50,000 is below the practical floor for Nifty 50 trading; brokerage will dominate).

**Why these defaults:** User explicitly requested robust market-driven, self-deciding behavior with stop loss. Conservative defaults chosen to make first live deployment survivable; strict backtest gate chosen to prevent overfit strategies from reaching paper-trading.

**How to apply:** When implementing or modifying any module, respect these constraints. Guardrails layer is non-negotiable — every order goes through it, no overrides. EOD-suggested parameter changes need user approval (or stay within pre-approved safe bounds) before next-day deployment.

See [[user-profile]], [[feedback-angel-one-data-only]], [[project-gate-failure]], [[project-ec2-deployment]].
