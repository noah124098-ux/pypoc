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

**ultracode**

All workflow launches from this repo are pre-approved. Use the `Workflow` tool freely without waiting for user confirmation. The keyword above activates multi-agent orchestration for this session.

**Start any session with:** `Workflow({ name: "master" })` to auto-assign all work.

## What this project is

An automated, regime-aware **paper-trading agent for NSE Nifty 50** that consumes live Angel One SmartAPI tick data, classifies the market into TREND/RANGE/VOLATILE regimes, runs strategy logic appropriate to the regime, and sends every order through a hard guardrails layer with stop-loss, daily-loss circuit, drawdown circuit, and black-swan halts.

**Status:** v3 — React+FastAPI deployed. Phases 3/4/5/5b/6b/7/7b/7c complete. 1219 tests passing. Gate at 0.11 Sharpe (failing due to data drift — stable baseline was 0.32; see Open issues). No live broker active. React+FastAPI dashboard at :8502 (11 tabs, auth: admin/pypoc2024).

**Model:** claude-opus-4-8 (default for all sessions). effortLevel: max.

**Repo:** https://github.com/noah124098-ux/pypoc

## Locked design decisions

| Decision | Locked value |
| --- | --- |
| Trading style | Multi-style, regime-aware (TREND / RANGE / VOLATILE) |
| Universe | Nifty 50 only |
| Risk model | Conservative — 1-2% per trade, max 5 positions, daily-loss circuit -3%, drawdown circuit -10% |
| Decision engine | Hybrid — rules trade autonomously intraday; Claude Opus 4-8 reviews trades EOD and proposes parameter tweaks |
| Mode | Paper-trade only for v1. No live broker active. |
| Deployment | Portable: local Windows for dev, EC2 Windows Server for 24/7 |
| Broker abstraction | `IBroker` interface; `PaperBroker` impl + `AngelOneLiveBroker` stub |
| Live data source | Angel One SmartAPI (`core/data/angelone_feed.py` + `angelone_history.py`) |
| Backtest data fallback | NSE Bhavcopy archives (`core/data/bhavcopy.py`), no creds needed |
| MCP integration | Yes — `mcp_server/` exposes read-only tools |
| Backtest gate thresholds | Sharpe ≥ 1.2, MaxDD ≤ 15%, win rate ≥ 45%, profit factor ≥ 1.5, ≥100 trades, 3+ years walk-forward, file ≤30 days old |
| Primary dashboard | React + FastAPI — served at `:8502` (SPA) / `:8503` (dev Vite) |

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
  strategies/    8 strategies + IStrategy interface + indicators (Hurst, autocorr)
  risk/          Position sizing + 14 hard guardrails (every order goes through, no overrides)
  execution/     Orchestrator (live loop), command_queue.py (file-based MCP mutations)
  persistence/   SQLite store with schema versioning + migrations
  llm/           EOD Claude reviewer, news_scorer.py (Claude sentiment scoring)
  notifications/ Telegram notifier (richer alerts w/ regime/SL/target/confidence),
                 email notifier (EOD HTML reports + halt alerts)
  config.py      Pydantic settings loaded from YAML + .env, hot-reload for risk params
api/             FastAPI backend — REST + WebSocket, HTTP Basic Auth, request logging
  main.py        All /api/* endpoints + /ws WebSocket broadcast loop + SPA static serving
  run.py         Uvicorn entrypoint for :8502
frontend/        React dashboard (primary) — 11 tabs, mobile-responsive, dark theme
  src/           TypeScript source (App.tsx, pages/, hooks/)
  dist/          Production build served by FastAPI at :8502
mcp_server/      MCP server (read-only) — tools for inspecting the live agent
dashboard/       Streamlit dashboard components — DECOMMISSIONED (kept for reference)
dashboard.py     Streamlit entry point — DECOMMISSIONED
deploy/          nginx SSL/TLS config + self-signed cert generator + EC2 setup scripts
docker-compose.yml  Docker Compose service definitions
Dockerfile       Multi-stage build: venv + React build + FastAPI
tests/           1219 passing tests (includes Vitest frontend unit tests via pytest adapter)
config/
  default.yaml   Default config (TREND strategies currently disabled for gate baseline)
  environments/  dev.yaml, staging.yaml, prod.yaml overrides
cli.py           Entry points: run | warmup | check-config | mcp-server | backtest |
                 walk-forward | check-gate | preflight | status | performance |
                 strategy-report | schedule-gate-refresh
scripts/         Windows startup/ops scripts: start_agent.bat, start_dashboard.bat,
                 start_mcp.bat, run_walkforward.bat, health_check.ps1,
                 monitor_paper.py, watch_trades.py,
                 refresh_gate.bat, refresh_gate.ps1,
                 backup_data.bat, rotate_logs.bat
docs/            ARCHITECTURE.md, LIVE_BROKER_SETUP.md
.env.example     Credentials template (Angel One + Upstox + Telegram + email + DASHBOARD_PASSWORD)
```

## How to run anything

```powershell
# Activate venv (Windows)
.\.venv\Scripts\Activate.ps1

# Run the full test suite (must always be 1219/1219)
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

# --- React + FastAPI dashboard ---

# Production: FastAPI serves React SPA + API on port 8502
# Set DASHBOARD_PASSWORD in .env (default: pypoc2024)
python api/run.py

# Open browser: http://localhost:8502
# API docs (Swagger): http://localhost:8502/docs

# Development: Vite dev server (hot-reload) on port 5173
cd frontend && npm run dev
# API still needs to run separately: python api/run.py

# Build React for production (output to frontend/dist)
cd frontend && npm run build
```

## Service ports

| Port | Service |
| --- | --- |
| 8502 | FastAPI backend + React SPA (production) |
| 5173 | Vite dev server (frontend hot-reload) |
| 8765 | MCP server |

## Open issues — pick up here

### 1. Backtest gate failing — 0.105 Sharpe

Current gate run (file timestamp 2026-06-04, all strategies enabled):

```text
Sharpe: 0.105, MaxDD: 12.4%, win: 38.0%, pf: 1.31, trades: 163
Gate FAILED: sharpe (0.105 < 1.2), win_rate (38.0% < 45%), profit_factor (1.31 < 1.5)
```

**Best known baseline (all strategies enabled + VIX<18 + Hurst H>0.5 + breadth 50% + 1.5x RANGE boost):** Sharpe 0.32, MaxDD 12.4%, win 38.6%, PF 1.42 — still failing gate.

**Root cause:** W3 (May 2025–Jun 2026) is a correction+recovery market. Long-only trend
strategies fail: `trend_breakout` and `rsi_momentum` generate heavy losses. W3 ends
mid-2026; a new walk-forward window starting 2025-08 may capture the recovery rally.

**What's been tried that DEGRADES results** (see memory `project_gate_status.md`):
every stock-level DMA filter, 52-week-high filter, regime directionality check,
rolling autocorr filter, disabling strategies, supertrend enable — all degrade results.

**Reproducible gate run:** `python cli.py walk-forward --years 3 --end-date 2026-05-29`

**Recommended next move:** Run `Workflow({ name: "gate-fix" })` to fan out parallel experiments.

**Expected gate pass:** ~August 2026 when W3 rolls out of the 3-year window and the recovery
rally (trending character) enters the walk-forward period.

### 2. Phase status

| Phase | What | Status |
| --- | --- | --- |
| 3 | Live NSE data feeds (VIX, PCR), economic calendar blackouts, EOD Claude reviewer | **DONE** |
| 4 | EOD reviewer (Claude Opus 4.7) producing parameter-adjustment proposals | **DONE** |
| 5 | Streamlit dashboard + Telegram alerts + email EOD report + dark mode | **DONE** |
| 5b | React dashboard (11 tabs incl. Angel One connect, mobile, FastAPI backend, HTTP Basic Auth, WebSocket) — Streamlit decommissioned | **DONE** |
| 6b | MCP mutating tools via file-based command-queue (halt_agent, place_paper_order) | **DONE** |
| 7 | AngelOneLiveBroker + Upstox V3 feed + hot-reload risk params + SQLite migrations | **DONE** |
| 7b | FastAPI production hardening: WebSocket manager, SSL/TLS nginx, auth, status endpoint | **DONE** |
| 7c | Advanced dashboard: correlation matrix, scatter, waterfall, AI commentary, TOTP 2FA, Prometheus metrics, cursor pagination, code-split React, system health page | **DONE** |
| 8 | Credentials wired + paper trading 30-day proof + gate pass | not started |

**Next steps for Phase 8:**
1. Wire Angel One + Upstox credentials into `.env` (see `.env.example`)
2. Run `python cli.py preflight` to validate all feeds
3. Start paper trading: `python cli.py run`
4. Monitor for 30 days via dashboard at `:8502`
5. Gate pass expected ~August 2026 (W3 correction exits walk-forward window)
6. After gate pass + 30-day paper proof → Phase 8 (live, small capital)

### 3. Recently completed (last 15 commits)

- `f66571b` fix(frontend): handle paginated API responses and prevent blank screens
- `2f55724` fix(frontend): proper title, meta tags, favicon for React dashboard
- `b99174f` fix(config): restore clean baseline config — signal_cooldown 30min, vix_spike_bounce enabled
- `905dc30` feat(strategies): add VolumeBreakoutConfirm + GapAndHold (disabled after gate regression)
- `4893086` experiment(gate): bb_squeeze target_r_multiple 2.0->3.0 improves Sharpe 0.036->0.124
- `db1518d` chore: 10-hour session final state — 1202 tests, gate 0.105 Sharpe, React dashboard at :8502
- `83361d0` feat(api): Prometheus /api/metrics endpoint, /api/ready readiness probe, structured JSON request logging
- `7f05978` feat(analytics): correlation matrix, scatter plot, waterfall chart in dashboard
- `18a667f` feat(ai): POST /api/ai/commentary + Live Commentary UI in AiReviewTab
- `e51ed85` feat(auth): optional TOTP 2FA for dashboard login
- `0913803` feat(api): server-side TTL cache on expensive FastAPI endpoints
- `802d41e` feat(dashboard): System health page and API docs page in React frontend
- `a78186f` feat(frontend): search/filter UI on Positions and Replay tabs
- `99056a2` feat(api): cursor-based pagination + /api/trades/stats
- `7cbf547` test: coverage boost from 85% to 92% (+266 tests, total 1202)

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
