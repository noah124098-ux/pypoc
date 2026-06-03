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

## Service Ports

| Port | Service | Notes |
| --- | --- | --- |
| 8502 | FastAPI backend + React SPA | Production; HTTP Basic Auth on all /api/* routes |
| 5173 | Vite dev server | Frontend hot-reload (dev only) |
| 8765 | MCP server | Read-only + mutating tools via command queue |

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
| `core/data/nse_fii_dii.py` | FII/DII institutional flows scraper |
| `core/data/economic_calendar.py` | Economic event calendar for trade-day filtering |
| `core/data/nse_rate_limiter.py` | Rate limiter + circuit breaker + retry for NSE API feeds |
| `core/data/upstox_feed.py` | Upstox V3 WebSocket feed (alternative data source) |
| `core/data/angelone_portfolio.py` | Read-only Angel One portfolio fetcher |
| `core/llm/eod_reviewer.py` | EOD Claude reviewer — analyses trades, proposes parameter tweaks |
| `core/llm/news_scorer.py` | Claude Haiku news sentiment scoring |
| `core/notifications/telegram.py` | Telegram alerts (regime, SL/target, confidence + halt/daily summary) |
| `core/notifications/email.py` | Email EOD reports and halt alerts |
| `core/regime/classifier.py` | ADX + Bollinger Band width + VIX regime classifier |
| `core/risk/guardrails.py` | 14 hard guardrails checked on every order — no overrides |
| `core/risk/position_sizer.py` | ATR-based position sizing (1-2% risk per trade) |
| `core/strategies/indicators.py` | Technical indicators incl. Hurst exponent + rolling autocorrelation |
| `core/strategies/` | 8 strategies: `trend_breakout`, `rsi_momentum`, `ema_crossover`, `mean_reversion`, `bb_squeeze`, `obv_trend`, `supertrend`, `volatility_compression` |
| `core/execution/orchestrator.py` | Live loop: ticks -> regime -> strategies -> guardrails -> broker |
| `core/execution/command_queue.py` | File-based command queue consumed by orchestrator on each tick |
| `core/persistence/store.py` | SQLite store for trades, positions, equity curve |
| `core/config.py` | Pydantic settings loaded from `config/default.yaml` + `.env` |
| `api/main.py` | FastAPI app — all /api/* endpoints, WebSocket broadcast, SPA static serving, HTTP Basic Auth |
| `api/run.py` | Uvicorn entrypoint for port 8502 |
| `frontend/src/` | React + TypeScript SPA — 10 tabs, Recharts, mobile-responsive |
| `frontend/dist/` | Production build served by FastAPI |
| `backtest/engine.py` | Backtest engine (reuses live components verbatim) |
| `backtest/walk_forward.py` | Walk-forward harness — rolling 12-month windows |
| `backtest/gate.py` | Gate checker — enforces pass/fail thresholds |
| `mcp_server/server.py` | MCP server process (read-only + mutating tools via command queue) |
| `mcp_server/tools.py` | MCP tool implementations |
| `deploy/nginx.conf` | nginx reverse-proxy config |
| `deploy/nginx_ssl.conf` | nginx SSL/TLS config with self-signed cert support |
| `docker-compose.yml` | Docker Compose — agent + api + mcp services |
| `cli.py` | Entry points: `run`, `backtest`, `walk-forward`, `check-gate`, `mcp-server` |

## Data Flow

```
Angel One WebSocket (tick data)
        |
        v
   Tick Aggregator  <-- Nifty daily OHLC (Bhavcopy / Angel One history)
        |                        |
        |              Regime Classifier <-- NSE VIX, NSE PCR, NSE FII-DII
        |                        |
        v                        v
   Strategies  <-----------  Orchestrator
   (8 strategies:                |
    trend_breakout,              v
    rsi_momentum,         Command Queue  <-- MCP mutating tools (place_paper_order, halt)
    ema_crossover,
    mean_reversion,
    bb_squeeze,
    obv_trend,
    supertrend,
    volatility_compression)
        |
        v
   Guardrails (14 checks — no overrides)
        |
        v
   PaperBroker (fills with slippage; charges calculator)
        |
        v
   SQLite Store
        |
        +-----------> MCP Server (:8765, read-only) --> Claude Desktop / external tools
        |
        +-----------> FastAPI (:8502)
        |                   |
        |                   +----> /api/* REST endpoints (JSON, HTTP Basic Auth)
        |                   +----> /ws  WebSocket live-push (token auth)
        |                   +----> /*   React SPA static files
        |
        +-----------> React Dashboard (:8502 prod / :5173 dev)
        |             (10 tabs: Overview, P&L, Regime, Backtest, Portfolio,
        |              Signals, Costs, Controls, AI Review, Replay)
        |
        +-----------> EOD Claude Reviewer --> Telegram / Email
```

## React + FastAPI Dashboard

### Stack
- **Frontend:** React 18, TypeScript, Recharts, Tailwind CSS
- **Backend:** FastAPI (Python) + Uvicorn, served on `:8502`
- **Auth:** HTTP Basic Auth on all `/api/*` routes; WebSocket token check
- **Build:** Vite — `npm run build` outputs to `frontend/dist/`, served as static files by FastAPI

### Dashboard Tabs (10)
| Tab | Content |
| --- | --- |
| Overview | Equity sparkline, regime badge, live P&L, key metrics header bar |
| P&L | Detailed equity curve with drawdown subplot + trade P&L markers |
| Regime | Current regime, regime history timeline, config thresholds |
| Backtest | Walk-forward gate results, per-window Sharpe/DD/win/PF |
| Portfolio | Open positions, unrealized P&L |
| Signals | Recent strategy signals, guardrail outcomes |
| Costs | Brokerage + STT + exchange charges breakdown |
| Controls | Risk param hot-reload, strategy enable/disable, halt/resume agent |
| AI Review | EOD Claude reviewer output — trade analysis + parameter proposals |
| Replay | Trade replay with configurable speed |

### Key API Endpoints
| Endpoint | Method | Description |
| --- | --- | --- |
| `/api/status` | GET | Agent status, uptime, regime, equity |
| `/api/pnl` | GET | P&L time series |
| `/api/trades` | GET | Trade history |
| `/api/positions` | GET | Open positions |
| `/api/signals` | GET | Recent signals |
| `/api/backtest` | GET | Gate results from `data/backtest_gate.json` |
| `/api/costs` | GET | Charges summary |
| `/ws` | WS | Live data push (snapshot every 5s) |
| `/docs` | GET | Swagger UI (auto-generated) |

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

| Metric | Threshold | Current (2026-06-03) |
| --- | --- | --- |
| Sharpe ratio | >= 1.2 | -0.10 (FAIL) |
| Max drawdown | <= 15% | 3.4% (pass) |
| Win rate | >= 45% | 49.1% (pass) |
| Profit factor | >= 1.5 | 2.16 (pass) |
| Trade count | >= 100 | 53 (FAIL) |
| Walk-forward span | >= 3 years | 3 years (pass) |
| Gate file age | <= 30 days | fresh (pass) |

`cli.py run` refuses to start live paper-trading until all thresholds are met and the gate
file is fresh. The gate is currently failing on `sharpe` and `n_trades`. The TREND strategies
are currently disabled in config; re-enabling them restores Sharpe to ~0.32 but still fails.
Full fix-path and experiment history in `CLAUDE.md` under "Open issues" and in
`memory/project_gate_status.md`.
