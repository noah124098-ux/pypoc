# CLAUDE.md — handoff for any Claude Code session in this repo

> Read this first. It captures the current state, locked decisions, and what to work on next so a new session doesn't have to re-derive context from code alone.

## What this project is

An automated, regime-aware **paper-trading agent for NSE Nifty 50** that consumes live Angel One SmartAPI tick data, classifies the market into TREND/RANGE/VOLATILE regimes, runs strategy logic appropriate to the regime, and sends every order through a hard guardrails layer with stop-loss, daily-loss circuit, drawdown circuit, and black-swan halts.

**Status:** v1 paper-trading skeleton. **No live broker integration.** Backtest gate must pass before any live deployment is even attempted, and the gate currently fails (see "Open issues" below).

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
  data/          Angel One feed + history, Bhavcopy fallback, tick aggregator, universe
  regime/        Regime classifier (ADX + BB width + VIX)
  strategies/    Three baseline strategies + IStrategy interface
  risk/          Position sizing + 14 hard guardrails (every order goes through, no overrides)
  execution/     Orchestrator (live loop wires everything)
  persistence/   SQLite store
  config.py      Pydantic settings loaded from YAML + .env
mcp_server/      MCP server (read-only) — 10 tools for inspecting the live agent
tests/           80 passing tests, exhaustive guardrail coverage
config/          Default YAML
cli.py           Entry points: run | warmup | check-config | mcp-server | backtest | walk-forward | check-gate
```

## How to run anything

```powershell
# Activate venv (Windows)
.\.venv\Scripts\Activate.ps1

# Run the full test suite (must always be 80/80)
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

### 1. Backtest gate failed; agent is barely trading

Walk-forward result on 3 years of Nifty 50 daily data:

```text
W1 (2023-05 -> 2024-05):  7 trades, Sharpe 1.33, MaxDD 4.8%, win 71%, pf 12.2
W2 (2024-05 -> 2025-05):  1 trade,  Sharpe -6.78, MaxDD 1.9%, win 0%
W3 (2025-05 -> 2026-05):  1 trade,  Sharpe -3.76, MaxDD 3.7%, win 0%
Aggregate: 9 trades, Sharpe -0.25, MaxDD 4.82%, win 56%, pf 4.04
Gate FAILED on: sharpe (-0.25 < 1.2), n_trades (9 < 100)
```

When the agent trades, it's profitable. The problem is **it almost never trades.** 9 trades over 3 years across 50 stocks ≈ 3 trades/year.

**Likely root causes (in priority order):**
1. Regime classifier too strict — ADX > 25 + BB width filters most days into UNKNOWN
2. ATR stop multiplier (2.0×) trips per-trade-risk guardrail too often → qty rounds to 0
3. Donchian period 20 days only fires on cleanest breakouts
4. Engine assumes signals fire on D-1 close, fill on D's open + slippage; gap-up opens push stop above open → rejected pre-guardrails

**Recommended next move:** add a debug command that prints the full rejection breakdown (which guardrail rule rejected each signal, regime distribution, qty=0 rejections). Then we know exactly what to tune.

### 2. EC2 development environment

Windows Server EC2 at `3.239.215.143`. Setup completed:
- Git, Python 3.12, VS Code, Node.js installed via Chocolatey
- Repo cloned to `C:\Users\Administrator\pypoc`
- `.venv` created, `pip install -r requirements.txt` succeeded
- 80/80 tests pass
- OpenSSH server running but **port 22 currently blocked at AWS Security Group** (user's IP not whitelisted)

User decided to **not** use VS Code Remote-SSH — work happens directly on the EC2 over RDP, with VS Code + Claude Code running there.

### 3. Phases not yet built

| Phase | What |
| --- | --- |
| 3 | News sentiment scorer (Claude) + options PCR/OI feed + FII/DII flows |
| 4 | EOD reviewer (Claude Opus 4.7) producing parameter-adjustment proposals |
| 5 | Streamlit dashboard + Telegram alerts + email EOD report |
| 6b | MCP mutating tools (place_paper_order, halt_agent) via command-queue |
| 7 | Live broker integration (separate creds, separate guards) |
| 8 | Live deployment with small capital, after backtest gate + paper proof |

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
