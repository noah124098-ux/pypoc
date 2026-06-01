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

**Status:** v2 — Phases 3 (live NSE data feeds, economic calendar, EOD Claude reviewer), 5 (Streamlit dashboard with Telegram/email notifications), and 6b (file-based MCP command queue) are now **complete**. Phase 4 (EOD reviewer) is also live. Backtest gate still fails (see "Open issues" below). No live broker integration.

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
  broker/        IBroker + PaperBroker (slippage + brokerage + auto stop/target)
  data/          Angel One feed + history, Bhavcopy fallback, tick aggregator, universe,
                 economic_calendar.py (RBI/Budget/FOMC blackouts), nse_vix.py, nse_pcr.py
  regime/        Regime classifier (ADX + BB width + VIX)
  strategies/    Three baseline strategies + IStrategy interface
  risk/          Position sizing + 14 hard guardrails (every order goes through, no overrides)
  execution/     Orchestrator (live loop wires everything), command_queue.py (file-based MCP mutations)
  persistence/   SQLite store
  llm/           EOD Claude reviewer — daily trade analysis and parameter suggestions
  notifications/ Telegram notifier (trade alerts), email notifier (EOD reports and halt alerts)
  config.py      Pydantic settings loaded from YAML + .env
mcp_server/      MCP server (read-only) — 10 tools for inspecting the live agent
dashboard.py     Streamlit dashboard — equity curve, costs, regime timeline, controls + notification config
tests/           200 passing tests, exhaustive guardrail + NSE data module + notification coverage
config/          Default YAML
cli.py           Entry points: run | warmup | check-config | mcp-server | backtest | walk-forward | check-gate
```

## How to run anything

```powershell
# Activate venv (Windows)
.\.venv\Scripts\Activate.ps1

# Run the full test suite (must always be 200/200)
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

### 1. Backtest gate failed — W3 correction market hurts aggregate

Current best walk-forward (pinned `--end-date 2026-05-29`):

```text
W1 (2023-05 -> 2024-05):  ~80 trades, Sharpe ~1.67, MaxDD ~5%, win ~43%, pf ~1.78  ✓
W2 (2024-05 -> 2025-05):  ~43 trades, Sharpe ~0.20, MaxDD ~7%, win ~30%, pf ~1.32
W3 (2025-05 -> 2026-05):  ~59 trades, Sharpe ~-1.26, MaxDD ~10%, win ~29%, pf ~0.86
Aggregate: ~190 trades, Sharpe ~0.42, MaxDD ~10.5%, win ~35.8%, pf ~1.41
Gate FAILED: sharpe (0.42 < 1.2), win_rate (35.8% < 45%), profit_factor (1.41 < 1.5)
```

**Root cause:** W3 (May 2025–Jun 2026) is a correction+recovery market. All long-only
trend strategies fail in this period. Per-strategy W3 damage: `trend_breakout`
(-₹19,912, 20% win), `rsi_momentum` (-₹11,734, 0% win).

**What's been tried that DEGRADES results** (see memory `project_gate_status.md`):
every stock-level DMA filter, 52-week-high filter, regime directionality check —
all hurt W1 more than they help W3.

**Reproducible gate run:** `python cli.py walk-forward --years 3 --end-date 2026-05-29`

**Recommended next move:** Run `Workflow({ name: "gate-fix" })` to fan out parallel experiments.

### 2. EC2 development environment

Windows Server EC2 at `3.239.215.143`. Setup completed:
- Git, Python 3.12, VS Code, Node.js installed via Chocolatey
- Repo cloned to `C:\Users\Administrator\pypoc`
- `.venv` created, `pip install -r requirements.txt` succeeded
- 80/80 tests pass
- OpenSSH server running but **port 22 currently blocked at AWS Security Group** (user's IP not whitelisted)

User decided to **not** use VS Code Remote-SSH — work happens directly on the EC2 over RDP, with VS Code + Claude Code running there.

### 3. Phase status

| Phase | What | Status |
| --- | --- | --- |
| 3 | Live NSE data feeds (VIX, PCR), economic calendar blackouts, EOD Claude reviewer | **DONE** |
| 4 | EOD reviewer (Claude Opus 4.7) producing parameter-adjustment proposals | **DONE** |
| 5 | Streamlit dashboard + Telegram alerts + email EOD report | **DONE** |
| 6b | MCP mutating tools via file-based command-queue (halt_agent, place_paper_order) | **DONE** |
| 7 | Live broker integration (separate creds, separate guards) | not started |
| 8 | Live deployment with small capital, after backtest gate + paper proof | not started |

### 4. Recently completed (last 10 commits)

| Commit | What |
| --- | --- |
| 5ce5e28 | Orchestrator processes MCP command queue on each tick (Phase 6b) |
| f48ad35 | Dashboard: regime timeline chart + DMA status metrics |
| ad2a554 | File-based command queue for safe MCP mutations |
| 9393fef | Dashboard: Telegram + email config UI in Controls tab |
| 4a0fe2f | Orchestrator: wire Telegram halt + EOD notifications |
| 7e4f65d | Email notifier — EOD reports and halt alerts |
| 09f5932 | EOD Claude reviewer — daily trade analysis and parameter suggestions |
| 1833bac | Telegram notifier — trade alerts and daily summary |
| 3f04f8b | Economic calendar blackout — RBI/Budget/FOMC event guard |
| ee9b2a0 | Orchestrator: wire live VIX refresh and PCR sentiment filter |

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
