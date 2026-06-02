# NSE Nifty 50 Paper Trading Agent

Automated, regime-aware paper-trading agent for NSE Nifty 50. Consumes live Angel One SmartAPI tick data, classifies the market into TREND/RANGE/VOLATILE regimes, and runs the appropriate strategy. Every order passes through 14 hard guardrails (stop-loss, daily-loss circuit, drawdown circuit, black-swan halt). No live broker integration — paper trading only.

**Status: v2.** Phases 3–7 complete. Backtest gate currently failing (correction-market losses in W3 pull aggregate below thresholds). 348 tests passing.

---

## Architecture

```
Angel One WebSocket
        |
        v
   Tick Aggregator  <-- Daily OHLC (Bhavcopy / AngelOne history)
        |                         |
        |               Regime Classifier <-- NSE VIX, PCR, FII/DII
        |                         |
        v                         v
   Strategies  <-----------  Orchestrator
        |                         |
        v                         v
   Guardrails (14 rules)   Command Queue  <-- MCP mutating tools
        |
        v
   PaperBroker  (slippage + brokerage modeled)
        |
        +-----------> SQLite Store
        |                 |
        |                 +---> MCP Server (read-only, 10 tools)
        |                 +---> Streamlit Dashboard
        +-----------> EOD Claude Reviewer --> Telegram / Email
```

**Three strategies, each regime-gated:**

| Strategy | Regime | Signal |
| --- | --- | --- |
| `trend_breakout` | TREND | Donchian channel breakout + ATR stop |
| `mean_reversion` | RANGE | RSI + Bollinger Band reversion |
| `volatility_compression` | VOLATILE | NR7 breakout |

---

## Quick Start

```bash
# 1. Clone and create venv
git clone https://github.com/noah124098-ux/pypoc
cd pypoc
python -m venv .venv
. .venv/Scripts/activate        # Windows bash
pip install -r requirements.txt

# 2. Configure credentials (DATA-ONLY — see Security Notice below)
cp .env.example .env
# Fill in ANGEL_ONE_API_KEY, ANGEL_ONE_CLIENT_CODE, ANGEL_ONE_PASSWORD, ANGEL_ONE_TOTP_SECRET

# 3. Validate config and credentials
python cli.py check-config

# 4. Run the test suite (must be 348/348)
pytest -q

# 5. Run 3-year walk-forward backtest
python cli.py walk-forward --years 3 --end-date 2026-05-29

# 6. Check if gate passes
python cli.py check-gate

# 7. Start the dashboard (second terminal)
python -m streamlit run dashboard.py

# 8. Start the paper-trading agent (gate must pass first)
python cli.py run
```

**Get Angel One SmartAPI credentials:**

1. Open a free account at <https://www.angelone.in>
2. Go to <https://smartapi.angelbroking.com> → My Apps → Create an App (choose Trading APIs)
3. Note: API Key, Client Code (your Angel One ID), MPIN/Password
4. Enable TOTP in Angel One profile → "Enable TOTP for SmartAPI". Save the QR secret string as `ANGEL_ONE_TOTP_SECRET`.

---

## Key Commands

| Command | Description |
| --- | --- |
| `python cli.py check-config` | Validate config + credential fields |
| `python cli.py warmup` | Download daily history for regime + ADV |
| `python cli.py backtest --days 365` | Quick single-window backtest |
| `python cli.py walk-forward --years 3` | Full 3-year walk-forward (writes `data/backtest_gate.json`) |
| `python cli.py check-gate` | Print current gate pass/fail status |
| `python cli.py run` | Start live paper trading (gate must pass) |
| `python cli.py mcp-server` | Start MCP inspection server |
| `python cli.py health-check` | Structured health JSON; exit 0 = OK, 1 = degraded |
| `pytest -q` | Run all 348 tests |

---

## Dashboard

URL: `http://localhost:8501` (after `streamlit run dashboard.py`)

| Tab | Contents |
| --- | --- |
| Live | Positions, open P&L, last tick, regime badge, FII/DII sentiment |
| Performance | Equity curve, realized P&L, trade history, cost breakdown |
| Regime | TREND/RANGE/VOLATILE timeline chart, DMA status |
| Signals | Recent strategy signals (accepted + rejected by guardrails) |
| Market Pulse | VIX, PCR, FII/DII flow sidebar |
| Controls | Halt/resume agent, notification config (Telegram + email) |

---

## Backtest Gate Thresholds

The agent refuses to start until a fresh passing gate file exists in `data/backtest_gate.json`.

| Metric | Threshold |
| --- | --- |
| Sharpe ratio | >= 1.2 |
| Max drawdown | <= 15% |
| Win rate | >= 45% |
| Profit factor | >= 1.5 |
| Trade count | >= 100 |
| Walk-forward span | >= 3 years |
| Gate file age | <= 30 days |

**Current status (as of 2026-06-02):** Gate failing. W3 (May 2025–Jun 2026) correction market damages long-only trend strategies. Run `python cli.py walk-forward --years 3 --end-date 2026-05-29` to reproduce.

---

## Risk Configuration

Key knobs in `config/default.yaml`:

| Setting | Default | Description |
| --- | --- | --- |
| `capital.initial_inr` | 1000 | Starting capital (warning if < 50,000) |
| `risk.per_trade_risk_pct` | 1.0 | Max % of equity risked per trade |
| `risk.max_open_positions` | 5 | Hard cap on concurrent positions |
| `risk.daily_loss_circuit_pct` | 3.0 | Halt new entries if day P&L < -3% |
| `risk.drawdown_circuit_pct` | 10.0 | Halt + EOD review if drawdown > 10% |
| `risk.black_swan_nifty_move_pct` | 2.0 | Halt if Nifty moves > 2% in 15 min |

---

## MCP Server (Inspection)

The repo ships `.mcp.json` — Claude Code auto-discovers it. Run the agent first, then the MCP server reads `data/snapshot.json` and `data/agent.db` as read-only.

```bash
python cli.py mcp-server   # separate process from the agent
```

Tools exposed: `get_status`, `get_positions`, `get_pnl`, `get_recent_signals`, `get_recent_trades`, `get_guardrail_rejections`, `get_equity_curve`, `get_regime_history`, `get_universe`, `get_config_summary`

---

## Security Notice — DATA-ONLY Credentials

**Angel One credentials in this repo are for market data only.** Code-level guards in `core/data/angelone_feed.py` and `core/data/angelone_history.py` raise `RuntimeError` if any order method (`placeOrder`, `modifyOrder`, `cancelOrder`) is called after login. These guards must never be removed.

For live order execution (Phase 8): create a separate Angel One app with separate credentials. The data-feed guards stay in place regardless.

---

## Phase Status

| Phase | Description | Status |
| --- | --- | --- |
| 1–2 | Core engine, paper broker, guardrails, backtest gate | Done |
| 3 | Live NSE feeds (VIX, PCR), economic calendar, EOD reviewer | Done |
| 4 | EOD Claude Opus 4.7 reviewer — parameter proposals | Done |
| 5 | Streamlit dashboard, Telegram alerts, email EOD reports | Done |
| 6b | MCP mutating tools via file-based command queue | Done |
| 7 | Upstox feed stub + AngelOne live broker scaffold | Done (stub) |
| 8 | Live deployment with small capital after gate + paper proof | Not started |

---

## Project Layout

```text
core/
  broker/        PaperBroker + IBroker interface + angelone_live stub
  data/          AngelOne feed/history, Bhavcopy, tick aggregator, universe,
                 VIX, PCR, FII/DII flows, economic calendar, Upstox feed stub
  regime/        ADX + BB width + VIX regime classifier
  strategies/    8 strategies + IStrategy interface
  risk/          Position sizer + 14 guardrails (every order, no bypass)
  execution/     Orchestrator (live loop) + file-based command queue
  persistence/   SQLite store
  llm/           EOD reviewer + news sentiment scorer (Claude)
  notifications/ Telegram + email notifiers
  config.py      Pydantic settings (YAML + .env)
backtest/        Engine, walk-forward harness, gate checker
mcp_server/      Read-only MCP server (10 tools)
dashboard.py     Streamlit dashboard
scripts/         EC2 Windows startup scripts + health check
docs/            ARCHITECTURE.md, LIVE_BROKER_SETUP.md
config/          default.yaml
tests/           348 tests — guardrails covered exhaustively
cli.py           All entry points
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for full module map and data-flow detail.
