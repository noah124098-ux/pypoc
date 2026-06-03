# pypoc Architecture

## System Overview

pypoc is a regime-aware, automated paper-trading agent for NSE Nifty 50. It consumes live
Angel One SmartAPI tick data, classifies the market into TREND / RANGE / VOLATILE regimes,
runs strategy logic appropriate to each regime, and routes every order through a hard
guardrails layer (stop-loss, daily-loss circuit, drawdown circuit, black-swan halt).

All broker interaction in v1 is simulated via `PaperBroker`. No live orders are placed.
The backtest gate (Sharpe >= 1.2, MaxDD <= 15%, win >= 45%, PF >= 1.5, >= 100 trades over
3 years of walk-forward) must pass before any live deployment is attempted.

## Master/Worker Execution Model

Defined in `.claude/MASTER_WORKER.md`. Key rules:

- **Master (the Claude Code session) never waits idle.** The moment a worker is spawned,
  master immediately picks up the next task, plans the next phase, or handles local work.
- **Workers are spawned via the Workflow tool** using the named workflow files in
  `.claude/workflows/`:

  | Workflow | Purpose |
  | --- | --- |
  | `master.js` | Session startup: discover pending work, fan out workers, commit, push |
  | `gate-fix.js` | Parallel strategy experiments to fix a failing backtest gate |
  | `full-ci.js` | Full CI: pytest + gate check + lint + push |

- **Trigger at session start:** invoke `Workflow({ name: "master" })` — it runs the startup
  checklist (git status, open tasks, uncommitted worker output) and spawns the first batch
  of workers automatically.
- Workers commit after every meaningful change and push immediately. They report structured
  JSON results so master can triage quickly without reading logs.
- Parallelism budget: up to 16 concurrent workers per workflow phase.

## Module Map

| Path | Purpose |
| --- | --- |
| `core/broker/paper.py` | `PaperBroker` — simulates fills with slippage, auto stop/target |
| `core/broker/charges.py` | Brokerage + STT + exchange charges calculator |
| `core/data/angelone_feed.py` | Angel One SmartAPI WebSocket tick consumer (data-only, orders neutralized) |
| `core/data/angelone_history.py` | Angel One OHLC history fetcher (data-only, orders neutralized) |
| `core/data/bhavcopy.py` | NSE Bhavcopy daily archive downloader (no creds needed) |
| `core/data/tick_aggregator.py` | Aggregates ticks into OHLCV bars |
| `core/data/universe.py` | Nifty 50 symbol list and metadata |
| `core/data/nse_vix.py` | NSE India VIX scraper |
| `core/data/nse_pcr.py` | NSE options Put/Call Ratio scraper |
| `core/data/economic_calendar.py` | Economic event calendar for trade-day filtering |
| `core/llm/eod_reviewer.py` | EOD Claude reviewer — analyses trades, proposes parameter tweaks |
| `core/notifications/telegram.py` | Telegram alerts (halt notices, daily summaries) |
| `core/notifications/email.py` | Email EOD reports and halt alerts |
| `core/regime/classifier.py` | ADX + Bollinger Band width + VIX regime classifier |
| `core/risk/guardrails.py` | 14 hard guardrails checked on every order — no overrides |
| `core/risk/position_sizer.py` | ATR-based position sizing (1-2% risk per trade) |
| `core/strategies/` | 8 strategies: `trend_breakout`, `rsi_momentum`, `ema_crossover`, `mean_reversion`, `bb_squeeze`, `obv_trend`, `supertrend`, `volatility_compression` |
| `core/execution/orchestrator.py` | Live loop: ticks -> regime -> strategies -> guardrails -> broker |
| `core/command_queue.py` | File-based command queue consumed by orchestrator on each tick |
| `core/persistence/store.py` | SQLite store for trades, positions, equity curve |
| `core/config.py` | Pydantic settings loaded from `config/default.yaml` + `.env` |
| `backtest/engine.py` | Backtest engine (reuses live components verbatim) |
| `backtest/walk_forward.py` | Walk-forward harness — rolling 12-month windows |
| `backtest/gate.py` | Gate checker — enforces pass/fail thresholds |
| `mcp_server/server.py` | MCP server process (read-only + mutating tools via command queue) |
| `mcp_server/tools.py` | 10 MCP tool implementations |
| `frontend/` | React dashboard (primary) — 10 tabs served at :8503, backed by FastAPI at :8502 |
| `dashboard.py` | Streamlit dashboard — DECOMMISSIONED (service disabled; code kept for reference) |
| `cli.py` | Entry points: `run`, `backtest`, `walk-forward`, `check-gate`, `mcp-server` |

## Data Flow

```
Angel One WebSocket
        |
        v
   Tick Aggregator  <-- Nifty daily OHLC (Bhavcopy / Angel One history)
        |                        |
        |              Regime Classifier <-- NSE VIX, NSE PCR
        |                        |
        v                        v
   Strategies  <-----------  Orchestrator
        |                        |
        v                        v
   Guardrails            Command Queue  <-- MCP mutating tools (place_paper_order, halt)
        |
        v
   PaperBroker
        |
        v
   SQLite Store
        |
        +-----------> MCP Server (read-only) --> Claude Desktop / external tools
        |
        +-----------> React Dashboard (:8503) via FastAPI (:8502)
        |
        +-----------> EOD Claude Reviewer --> Telegram / Email
```

## Backtest Gate

The gate is enforced before any live trading. Run the walk-forward to regenerate
`data/backtest_gate.json`, then check it:

```bash
# Generate 3-year walk-forward result
python cli.py walk-forward --years 3 --window-months 12

# Check gate pass/fail
python cli.py check-gate
```

Pass thresholds (aggregate across all windows):

| Metric | Threshold |
| --- | --- |
| Sharpe ratio | >= 1.2 |
| Max drawdown | <= 15% |
| Win rate | >= 45% |
| Profit factor | >= 1.5 |
| Trade count | >= 100 |
| Walk-forward span | >= 3 years |
| Gate file age | <= 30 days |

`cli.py run` refuses to start live paper-trading until all thresholds are met and the gate
file is fresh. The gate is currently failing (Sharpe -0.25, 9 trades) due to the regime
classifier filtering out most days. The fix-path is in `CLAUDE.md` under "Open issues".
