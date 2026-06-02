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

**Status:** v2 — Phases 3 (live NSE data feeds, economic calendar, EOD Claude reviewer), 4 (EOD reviewer), 5 (Streamlit dashboard with Telegram/email notifications), 6b (file-based MCP command queue), and 7 (Upstox feed stub + angelone_live broker stub) are now **complete**. Backtest gate still fails (see "Open issues" below). No live broker integration active.

**Repo:** https://github.com/noah124098-ux/pypoc

## Locked design decisions

These were settled in conversation and are **not up for re-litigation** unless the user explicitly asks:

| Decision | Locked value |
| --- | --- |
| Trading style | Multi-style, regime-aware (TREND / RANGE / VOLATILE) |
| Universe | Nifty 50 only |
| Risk model | Conservative — 1-2% per trade, max 5 positions, daily-loss circuit -3%, drawdown circuit -10% |
| Decision engine | Hybrid — rules trade autonomously intraday; Claude Opus 4.7 reviews trades EOD and proposes parameter tweaks |
| Mode | Paper-trade only for v1. No live broker. |
| Deployment | Portable: local Windows for dev, EC2 Windows Server for 24/7 (in progress) |
| Broker abstraction | `IBroker` interface; `PaperBroker` is the only impl right now |
| Live data source | Angel One SmartAPI (`core/data/angelone_feed.py` for ticks, `core/data/angelone_history.py` for OHLC) |
| Backtest data fallback | NSE Bhavcopy archives (`core/data/bhavcopy.py`), no creds needed |
| MCP integration | Yes — `mcp_server/` exposes 10 read-only tools |
| Backtest gate thresholds | Sharpe ≥ 1.2, MaxDD ≤ 15%, win rate ≥ 45%, profit factor ≥ 1.5, ≥100 trades, 3+ years walk-forward, file ≤30 days old |

## Critical security constraint — DATA-ONLY

**Angel One credentials in this repo are for market data ONLY.** They must NEVER be used to place, modify, or cancel orders. Code-level guards in `core/data/angelone_feed.py` and `core/data/angelone_history.py` neutralize `placeOrder`, `modifyOrder`, `cancelOrder`, `placeOrderFullResponse` immediately after login. Any attempt to call them raises `RuntimeError("...DATA-ONLY...")`.

If a future phase needs live order execution: **do not remove these guards.** Add a separate broker module with its own credentials. The data-feed guards stay.

The constraint was set after the user accidentally pasted SmartAPI credentials in chat. The exposed key was neutralized via IP whitelist change to `1.1.1.1` on the offending app. New apps for production use must be created separately.

Test `test_order_methods_are_neutralized_after_login` in `tests/test_angelone_history.py` enforces this contract.

## Repo structure

```text
backtest/        Backtest engine (reuses Phase 1 components verbatim), walk-forward harness, strict gate
core/
  broker/        IBroker + PaperBroker (slippage + brokerage + auto stop/target) + angelone_live stub
  data/          Angel One feed + history, Bhavcopy fallback, tick aggregator, universe,
                 economic_calendar.py (RBI/Budget/FOMC blackouts), nse_vix.py, nse_pcr.py,
                 nse_fii_dii.py (institutional flows), upstox_feed.py (Upstox feed stub),
                 angelone_portfolio.py, historical.py, feed_base.py
  regime/        Regime classifier (ADX + BB width + VIX)
  strategies/    Three baseline strategies + IStrategy interface
  risk/          Position sizing + 14 hard guardrails (every order goes through, no overrides)
  execution/     Orchestrator (live loop wires everything), command_queue.py (file-based MCP mutations)
  persistence/   SQLite store
  llm/           EOD Claude reviewer — daily trade analysis and parameter suggestions
                 news_scorer.py — Claude-based news sentiment scoring
  notifications/ Telegram notifier (trade alerts), email notifier (EOD reports and halt alerts)
  config.py      Pydantic settings loaded from YAML + .env
mcp_server/      MCP server (read-only) — 10 tools for inspecting the live agent
dashboard.py     Streamlit dashboard — equity curve, costs, regime timeline, controls + notification config
tests/           348 passing tests, exhaustive guardrail + NSE data module + notification coverage
config/          Default YAML
cli.py           Entry points: run | warmup | check-config | mcp-server | backtest | walk-forward | check-gate
scripts/         Windows startup scripts (start_agent.bat, start_dashboard.bat, start_mcp.bat,
                 run_walkforward.bat, health_check.ps1) for EC2 deployment
docs/            ARCHITECTURE.md (full system design), LIVE_BROKER_SETUP.md (Phase 7 guide)
```

## How to run anything

```powershell
# Activate venv (Windows)
.\.venv\Scripts\Activate.ps1

# Run the full test suite (must always be 348/348)
pytest -q

# Inspect current config + creds
python cli.py check-config

# Run a single backtest over the last N days
python cli.py backtest --days 365

# Run walk-forward and write data/backtest_gate.json
python cli.py walk-forward --years 3 --window-months 12

# Check current gate status
python cli.py check-gate

# Run the live paper-trading agent (needs Angel One creds in .env)
python cli.py run

# Run the MCP server (separate process; reads data/snapshot.json + data/agent.db)
python cli.py mcp-server
```

## Open issues — pick up here

### 1. Backtest gate failed — correction market hurts aggregate

Current gate run (as of 2026-06-02, pinned `--end-date 2026-05-29`):

```text
Aggregate: 154 trades, Sharpe -0.31, MaxDD 19.6%, win 33.8%, pf 1.11
Gate FAILED: sharpe (-0.31 < 1.2), max_drawdown_pct (19.6% > 15%), win_rate (33.8% < 45%), profit_factor (1.11 < 1.5)
```

**Root cause:** The 2025–2026 correction+recovery market severely damages long-only
trend strategies. Both `trend_breakout` and `rsi_momentum` generate heavy losses in
the correction window with near-zero win rates.

**What's been tried that DEGRADES results** (see memory `project_gate_status.md`):
every stock-level DMA filter, 52-week-high filter, regime directionality check,
rolling autocorr filter — all hurt earlier windows more than they help the correction window.
The autocorr filter commit was reverted (c0136df).

**Reproducible gate run:** `python cli.py walk-forward --years 3 --end-date 2026-05-29`

**Recommended next move:** Run `Workflow({ name: "gate-fix" })` to fan out parallel experiments.

### 2. EC2 development environment

Windows Server EC2 at `3.239.215.143`. Setup completed:
- Git, Python 3.12, VS Code, Node.js installed via Chocolatey
- Repo cloned to `C:\Users\Administrator\pypoc`
- `.venv` created, `pip install -r requirements.txt` succeeded
- 348/348 tests pass
- Startup scripts in `scripts/` for agent, dashboard, MCP, walk-forward, and health check
- OpenSSH server running but **port 22 currently blocked at AWS Security Group** (user's IP not whitelisted)

User decided to **not** use VS Code Remote-SSH — work happens directly on the EC2 over RDP, with VS Code + Claude Code running there.

### 3. Phase status

| Phase | What | Status |
| --- | --- | --- |
| 3 | Live NSE data feeds (VIX, PCR), economic calendar blackouts, EOD Claude reviewer | **DONE** |
| 4 | EOD reviewer (Claude Opus 4.7) producing parameter-adjustment proposals | **DONE** |
| 5 | Streamlit dashboard + Telegram alerts + email EOD report | **DONE** |
| 6b | MCP mutating tools via file-based command-queue (halt_agent, place_paper_order) | **DONE** |
| 7 | Live broker integration stub — Upstox feed + angelone_live broker scaffold | **DONE (stub)** |
| 8 | Live deployment with small capital, after backtest gate + paper proof | not started |

### 4. Recently completed (last 10 commits)

| Commit | What |
| --- | --- |
| c0136df | Revert rolling autocorr filter — degraded W1/W2, net negative for gate |
| d9d28bc | fix: add UPSTOX_API_KEY/ACCESS_TOKEN to Secrets model for Upstox feed |
| 7d9a614 | fix: restore best-known config baseline + add Upstox feed stub |
| 523d018 | ops: Windows startup scripts + health check for EC2 deployment |
| a0c807c | fix(tests): fix test_get_config_summary after new config fields added |
| 22979ab | fix(tests): correct synthetic data for bb_squeeze, supertrend smoke tests |
| bbdb7f5 | feat(regime): add rolling autocorr filter to TREND classification (reverted) |
| bc7c55e | feat(dashboard): Market Pulse sidebar with VIX, PCR, and FII sentiment |
| fd6476c | feat(dashboard): FII/DII institutional flows panel in Live tab |
| 3104b56 | feat(orchestrator): FII/DII institutional sentiment gate for TREND BUYs |

## Maintenance

Scripts in `scripts/` for routine EC2 upkeep:

| Script | What it does | How to run |
| --- | --- | --- |
| `backup_data.bat` | Copies `agent.db`, `backtest_gate.json`, and `config/default.yaml` to `C:\Users\Administrator\pypoc_backups\YYYY-MM-DD\` | Run daily via Task Scheduler |
| `rotate_logs.bat` | Deletes `logs/*.log` and `logs/*.jsonl` files older than 7 days | Run daily via Task Scheduler |
| `health_check.ps1` | Emits structured JSON covering snapshot freshness, disk space (warn < 1 GB), memory (warn > 80 %), and agent.log size (warn > 100 MB); exits 0 = healthy, 1 = degraded | Called by monitoring tools or Task Scheduler |

**Setting up Task Scheduler tasks (run as Administrator):**

```powershell
# Daily backup at 06:00 IST
schtasks /create /tn "pypoc_backup" /tr "C:\Users\Administrator\pypoc\scripts\backup_data.bat" /sc daily /st 06:00 /ru SYSTEM /f

# Daily log rotation at 06:05 IST
schtasks /create /tn "pypoc_rotate_logs" /tr "C:\Users\Administrator\pypoc\scripts\rotate_logs.bat" /sc daily /st 06:05 /ru SYSTEM /f

# Health check every 5 minutes (output captured by your monitoring agent)
schtasks /create /tn "pypoc_health" /tr "powershell -NonInteractive -File C:\Users\Administrator\pypoc\scripts\health_check.ps1" /sc minute /mo 5 /ru SYSTEM /f
```

**Backup retention:** Backups accumulate under `C:\Users\Administrator\pypoc_backups\`. Manually prune or extend `backup_data.bat` to call `forfiles` there if disk is constrained.

## User preferences (collaboration style)

- Likes brainstorming via short Q&A before implementation. Use `AskUserQuestion` for genuine forks.
- Wants concise updates. Don't narrate internal deliberation.
- Approves of pushback when it's load-bearing (security, correctness). Do push back, but make it actionable.
- Has accepted security risks before with explicit confirmation; respect that decision while keeping the structural mitigations (DATA-ONLY guards) in place.
- The user **specifically asked for** robust guardrails, market-driven decisions, self-deciding behavior with stop loss. Every change should preserve those properties.

## Things to NOT do

- Never weaken the guardrails to make the gate pass. Tune strategy parameters or add new strategies, never relax safety rules.
- Never remove the DATA-ONLY guards on Angel One credentials. Add separate broker creds for live execution.
- Never log credential values. Never echo them to the user.
- Never run live trading until the backtest gate passes a fresh walk-forward (≤30 days old). The check is enforced in `cli.py cmd_run`.
- Never bypass `Guardrails.check()` from anywhere — every order must pass through it. Tests verify this.
