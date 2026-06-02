# CLAUDE.md — handoff for any Claude Code session in this repo

> Read this first. It captures the current state, locked decisions, and what to work on next so a new session doesn't have to re-derive context from code alone.

## ⚡ Operating Mode — MANDATORY

Every session in this repo runs as a **non-blocking master/worker system**.
Full protocol: `.claude/MASTER_WORKER.md` — read it immediately after this file.

**TL;DR:**
- Master assigns tasks to workers (via `Workflow` or `Agent`). Master NEVER waits idle.
- While workers run, master plans next steps or spawns more workers.
- Workers self-approve decisions — do NOT escalate to the user.
- Commit + push after every meaningful change. No end-of-session batches.
- Workers always run `pytest` before committing.

**Reusable workflows (in `.claude/workflows/`):**
- `master.js` — full session orchestrator: discover work → fan out → integrate
- `gate-fix.js` — parallel backtest gate improvement experiments
- `full-ci.js` — tests + gate + push pipeline

**Start any session with:** `Workflow({ name: "master" })` to auto-assign all work.

## What this project is

An automated, regime-aware **paper-trading agent for NSE Nifty 50** that consumes live Angel One SmartAPI tick data, classifies the market into TREND/RANGE/VOLATILE regimes, runs strategy logic appropriate to the regime, and sends every order through a hard guardrails layer with stop-loss, daily-loss circuit, drawdown circuit, and black-swan halts.

**Status:** v2 — Phases 3/4/5/6b/7 complete. 624 tests passing. Gate at 0.32 Sharpe (still failing — see Open issues). No live broker active.

**Repo:** https://github.com/noah124098-ux/pypoc

## Locked design decisions

| Decision | Locked value |
| --- | --- |
| Trading style | Multi-style, regime-aware (TREND / RANGE / VOLATILE) |
| Universe | Nifty 50 only |
| Risk model | Conservative — 1-2% per trade, max 5 positions, daily-loss circuit -3%, drawdown circuit -10% |
| Decision engine | Hybrid — rules trade autonomously intraday; Claude Opus 4.7 reviews trades EOD and proposes parameter tweaks |
| Mode | Paper-trade only for v1. No live broker active. |
| Deployment | Portable: local Windows for dev, EC2 Windows Server for 24/7 |
| Broker abstraction | `IBroker` interface; `PaperBroker` impl + `AngelOneLiveBroker` stub |
| Live data source | Angel One SmartAPI (`core/data/angelone_feed.py` + `angelone_history.py`) |
| Backtest data fallback | NSE Bhavcopy archives (`core/data/bhavcopy.py`), no creds needed |
| MCP integration | Yes — `mcp_server/` exposes 10 read-only tools |
| Backtest gate thresholds | Sharpe ≥ 1.2, MaxDD ≤ 15%, win rate ≥ 45%, profit factor ≥ 1.5, ≥100 trades, 3+ years walk-forward, file ≤30 days old |

## Critical security constraint — DATA-ONLY

**Angel One credentials in this repo are for market data ONLY.** They must NEVER be used to place, modify, or cancel orders. Code-level guards in `core/data/angelone_feed.py` and `core/data/angelone_history.py` neutralize `placeOrder`, `modifyOrder`, `cancelOrder`, `placeOrderFullResponse` immediately after login. Any attempt to call them raises `RuntimeError("...DATA-ONLY...")`.

If a future phase needs live order execution: **do not remove these guards.** Add a separate broker module with its own credentials. The data-feed guards stay.

Test `test_order_methods_are_neutralized_after_login` in `tests/test_angelone_history.py` enforces this contract.

## Repo structure

```text
backtest/        Backtest engine, walk-forward harness, strict gate
core/
  analytics/     Extended metrics, performance reports
  broker/        IBroker + PaperBroker + AngelOneLiveBroker stub
  data/          Angel One feed + history, Bhavcopy, tick aggregator, universe,
                 economic_calendar.py, nse_vix.py, nse_pcr.py, nse_fii_dii.py,
                 nse_rate_limiter.py (rate limiter + circuit breaker),
                 upstox_feed.py (Upstox V3 WebSocket), angelone_portfolio.py
  regime/        Regime classifier (ADX + BB width + VIX)
  strategies/    Three baseline strategies + IStrategy interface
  risk/          Position sizing + 14 hard guardrails (every order goes through, no overrides)
  execution/     Orchestrator (live loop), command_queue.py (file-based MCP mutations)
  persistence/   SQLite store with schema versioning + migrations
  llm/           EOD Claude reviewer, news_scorer.py (Claude sentiment scoring)
  notifications/ Telegram notifier, email notifier (EOD HTML reports + halt alerts)
  config.py      Pydantic settings loaded from YAML + .env, hot-reload for risk params
mcp_server/      MCP server (read-only) — 10 tools for inspecting the live agent
dashboard.py     Streamlit dashboard — equity curve, regime timeline, live signals, dark mode
tests/           624 passing tests
config/          Default YAML
cli.py           Entry points: run | warmup | check-config | mcp-server | backtest |
                 walk-forward | check-gate | preflight | status | performance |
                 strategy-report | schedule-gate-refresh
scripts/         Windows startup/ops scripts: start_agent.bat, start_dashboard.bat,
                 start_mcp.bat, run_walkforward.bat, health_check.ps1,
                 monitor_paper.py, watch_trades.py,
                 refresh_gate.bat, refresh_gate.ps1,
                 backup_data.bat, rotate_logs.bat
docs/            ARCHITECTURE.md, LIVE_BROKER_SETUP.md
.env.example     Credentials template (Angel One + Upstox + Telegram + email)
```

## How to run anything

```powershell
# Activate venv (Windows)
.\.venv\Scripts\Activate.ps1

# Run the full test suite (must always be 624/624)
pytest -q

# Pre-flight check before paper trading
python cli.py preflight

# Inspect current config + creds
python cli.py check-config

# Live agent status
python cli.py status

# 30-day performance report
python cli.py performance

# Per-strategy breakdown table
python cli.py strategy-report

# Run a single backtest over the last N days
python cli.py backtest --days 365

# Run walk-forward and write data/backtest_gate.json
python cli.py walk-forward --years 3 --window-months 12

# Check current gate status (add --json for machine-readable output)
python cli.py check-gate

# Set up weekly gate-refresh cron
python cli.py schedule-gate-refresh

# Run the live paper-trading agent (needs Angel One creds in .env)
python cli.py run

# Run the MCP server (separate process; reads data/snapshot.json + data/agent.db)
python cli.py mcp-server
```

## Open issues — pick up here

### 1. Backtest gate failing — 0.32 Sharpe, Supertrend NaN bug

Current gate run (pinned `--end-date 2026-05-29`):

```text
Aggregate: Sharpe ~0.32, MaxDD ~10.5%, win ~35.8%, pf ~1.41
Gate FAILED: sharpe (0.32 < 1.2), win_rate (35.8% < 45%), profit_factor (1.41 < 1.5)
```

**Root cause:** W3 (May 2025–Jun 2026) is a correction+recovery market. Long-only trend
strategies fail: `trend_breakout` and `rsi_momentum` generate heavy losses. Both
`supertrend` strategies produce 0 trades due to a NaN bug — kept intentionally to
preserve the current 0.32 baseline until a correct rewrite is done.

**What's been tried that DEGRADES results** (see memory `project_gate_status.md`):
every stock-level DMA filter, 52-week-high filter, regime directionality check,
rolling autocorr filter — all hurt W1 more than they help W3.

**Reproducible gate run:** `python cli.py walk-forward --years 3 --end-date 2026-05-29`

**Recommended next move:** Run `Workflow({ name: "gate-fix" })` to fan out parallel experiments.
Fix the Supertrend NaN bug as a prerequisite — both strategies must generate trades.

### 2. Phase status

| Phase | What | Status |
| --- | --- | --- |
| 3 | Live NSE data feeds (VIX, PCR), economic calendar blackouts, EOD Claude reviewer | **DONE** |
| 4 | EOD reviewer (Claude Opus 4.7) producing parameter-adjustment proposals | **DONE** |
| 5 | Streamlit dashboard + Telegram alerts + email EOD report + dark mode | **DONE** |
| 6b | MCP mutating tools via file-based command-queue (halt_agent, place_paper_order) | **DONE** |
| 7 | AngelOneLiveBroker + Upstox V3 feed + hot-reload risk params + SQLite migrations | **DONE** |
| 8 | Live deployment with small capital, after backtest gate + paper proof | not started |

### 3. Recently completed (last 10 commits)

- `c5537e5` .env.example: expanded template with Upstox vars
- `0a3f5fa` dashboard: dark mode toggle + Plotly dark template
- `1ece4e8` scripts: monitor_paper.py + watch_trades.py utilities
- `311ade1` broker: on_exit callback + per-trade Telegram alerts
- `67b8643` data: NSE API rate limiter + circuit breaker + retry
- `466e9f5` cli: preflight 10-point check
- `466e9f5` logging: structured trade log, rotation, quiet hours
- `3bbc40f` test: +66 tests for NSE data + analytics (→ 624 total)
- `2291d66` docs: comprehensive README rewrite
- `e8648da` persistence: SQLite schema versioning + migrations

### 4. EC2 environment

Windows Server EC2 at `3.239.215.143`. Setup complete. Work happens over RDP. Port 22 blocked (by design).
Startup scripts in `scripts/` for agent, dashboard, MCP, walk-forward, and health check.

## Maintenance (EC2)

`backup_data.bat` (daily 06:00) · `rotate_logs.bat` (daily 06:05) · `health_check.ps1` (every 5 min) · `refresh_gate.bat` (weekly)

## User preferences & hard constraints

- Concise updates; brainstorm via Q&A before implementing forks.
- Preserve robust guardrails, market-driven decisions, self-deciding behavior with stop loss.
- Never weaken guardrails to pass the gate — tune strategies only.
- Never remove DATA-ONLY guards on Angel One credentials.
- Never log credential values. Never echo them.
- Never run live trading until gate passes a fresh walk-forward (≤30 days old).
- Never bypass `Guardrails.check()` — every order must pass through it.
