# NSE Trading Agent

Automated, regime-aware paper-trading agent for NSE (Nifty 50). Consumes live Angel One SmartAPI tick data, classifies the market into TREND/RANGE/VOLATILE regimes, and runs the appropriate strategy. Every order passes through a hard guardrails layer with stop-loss, daily-loss circuit, drawdown circuit, and black-swan halts.

> **Status: v2.** Phases 3–7 complete (live data feeds, EOD reviewer, Streamlit dashboard, Telegram/email alerts, MCP command queue, Upstox/AngelOne broker stubs). 348 tests passing. Backtest gate still failing — correction-market losses in W3 pull the aggregate below thresholds. No live broker integration active.

## Why this design

- **Robust:** every order goes through the guardrail layer in [core/risk/guardrails.py](core/risk/guardrails.py). Each rule has a unit test.
- **Market driven:** decisions are made on candle closes from real Angel One tick data, with regime classification on Nifty + VIX context.
- **Self-deciding:** rules execute autonomously intraday; an EOD reviewer (Claude Opus 4.7) audits trades and proposes parameter tweaks for next day (review needed before changes apply).
- **Stop loss:** every order requires a stop-loss at submission. Paper broker auto-exits when triggered. No exceptions.

## Quick start

```bash
# 1. Create venv and install
python -m venv .venv
. .venv/Scripts/activate          # Windows bash
pip install -r requirements.txt

# 2. Configure secrets
cp .env.example .env
# Edit .env with Angel One API key, client code, password, TOTP secret.

# 3. Validate config
python cli.py check-config

# 4. Run tests (especially guardrails — these MUST pass; 348/348 expected)
pytest -q

# 5. Warmup (downloads daily history for regime + ADV)
python cli.py warmup

# 6. Run the live agent (paper trading on live data)
python cli.py run

# 7. (Optional) Launch the Streamlit dashboard in a second terminal
streamlit run dashboard.py

# 8. (Optional) Run the MCP inspection server in a third terminal
python cli.py mcp-server
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for a full description of every module and how they wire together.

## Getting Angel One SmartAPI credentials

1. Open a free Angel One account at <https://www.angelone.in>
2. Go to <https://smartapi.angelbroking.com> → "My Apps" → "Create an App"
3. Choose **Trading APIs** as the app type
4. Note down: **API Key**, **Client Code** (your Angel One ID), **MPIN/Password**
5. Enable TOTP: in Angel One profile → "Enable TOTP for SmartAPI". **Save the QR-code secret string** — paste it as `ANGEL_ONE_TOTP_SECRET` in `.env`. The agent uses pyotp to auto-generate fresh codes each login.

## Configuration

All risk + execution parameters live in [config/default.yaml](config/default.yaml). Key knobs:

| Setting | Default | What it does |
| --- | --- | --- |
| `capital.initial_inr` | 1000 | **Warning emitted if < 50000.** Most Nifty 50 stocks are unbuyable below that. |
| `risk.per_trade_risk_pct` | 1.0 | Max % of equity at risk per trade |
| `risk.max_open_positions` | 5 | Hard cap on concurrent positions |
| `risk.daily_loss_circuit_pct` | 3.0 | Halt new entries if day P&L < -3% |
| `risk.drawdown_circuit_pct` | 10.0 | Halt + EOD review if peak-to-trough drawdown > 10% |
| `risk.black_swan_nifty_move_pct` | 2.0 | Halt if Nifty moves > 2% in 15 min |
| `risk.stale_tick_seconds` | 10 | Reject orders if no tick received for >10s |

## Architecture

```text
[Angel One WS]
      │
      ▼
[Tick aggregator] ──► 1m / 5m / 15m candle closes
      │
      ▼
[Regime classifier] (ADX, BB width, VIX) ──► TREND | RANGE | VOLATILE
      │
      ▼
[Strategies that support regime]
  ├─ trend_breakout (Donchian + ATR stop)
  ├─ mean_reversion (RSI + Bollinger)
  └─ volatility_compression (NR7 breakout)
      │
      ▼
[Position sizer]  (1% rule + 20% size cap)
      │
      ▼
[Guardrails]  ◄── 14 hard rules, every one tested
      │
      ▼
[Paper broker]  (slippage + brokerage modeled; auto stop/target exits)
      │
      ▼
[SQLite persistence]  (trades, signals, equity, regime, guardrail events)
```

## MCP server

The agent ships with a read-only MCP server so any MCP client (Claude Code, Claude Desktop) can inspect the live agent.

### How it's wired

```text
┌─────────────────────┐         data/snapshot.json        ┌──────────────────┐
│  Agent process      │ ───────── (atomic write) ───────► │ MCP server       │
│  (cli.py run)       │         data/agent.db (SQLite)    │ (stdio transport)│
└─────────────────────┘                                   └────────┬─────────┘
                                                                   │
                                                                   ▼
                                                          ┌──────────────────┐
                                                          │  Claude Code     │
                                                          └──────────────────┘
```

The agent and the MCP server are **separate processes**. The agent owns state; the server is purely read-only and can be restarted independently. This means an MCP client can never disturb the trading loop — it can only observe.

### Tools exposed

| Tool | Purpose |
| --- | --- |
| `get_status` | Live state: feed, equity, regime, halt status |
| `get_positions` | Open positions with stop-loss / target / unrealized P&L |
| `get_pnl` | Realized + unrealized P&L, day P&L %, drawdown % |
| `get_recent_signals` | Recent strategy signals (accepted + rejected) |
| `get_recent_trades` | Recently closed trades |
| `get_guardrail_rejections` | Which safety rules are firing |
| `get_equity_curve` | Time series of equity snapshots |
| `get_regime_history` | TREND/RANGE/VOLATILE history |
| `get_universe` | Configured trading universe |
| `get_config_summary` | Active risk + strategy config |

### Setup with Claude Code

The repo includes [.mcp.json](.mcp.json) — Claude Code picks this up automatically when you open the project. It launches `python -m mcp_server.server` over stdio.

```bash
# Install MCP SDK if not already
pip install mcp

# Run the agent (writes data/snapshot.json + data/agent.db)
python cli.py run

# In another terminal/window, Claude Code's MCP client will be able to call:
#   get_status, get_positions, get_pnl, get_recent_signals, ...
```

### Setup with Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "nse-trading-agent": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "c:/Users/VijayaramuprawinVenu/Desktop/VPC_Work"
    }
  }
}
```

### Why read-only for v1

Mutating tools (place order, halt/unhalt agent) need a command-queue so the trading loop polls and applies them inside its own thread of control. That's safer than letting MCP write directly to broker state. Coming in a later phase.

## Backtesting & the strict gate

The agent will refuse to start in `live` mode unless a recent passing walk-forward backtest exists. The gate is the single source of truth for "is this strategy ready for real money?"

### Run a quick backtest (last N days)

```bash
python cli.py backtest --days 365
```

### Run the walk-forward and write the gate file

```bash
python cli.py walk-forward --years 3 --window-months 12
```

This:

1. Downloads daily OHLC for the Nifty 50 universe + Nifty index proxy (yfinance/nsepython, cached)
2. Splits the period into non-overlapping windows (12 months each by default)
3. Runs an independent backtest per window using the SAME guardrails / sizing / strategies as production
4. Aggregates metrics across windows
5. Evaluates against [config/default.yaml](config/default.yaml) `backtest_gate` thresholds
6. Writes `data/backtest_gate.json`

### Gate thresholds (default)

| Metric | Threshold | Direction |
| --- | --- | --- |
| Sharpe ratio | >= 1.2 | greater |
| Max drawdown % | <= 15.0 | less |
| Win rate % | >= 45.0 | greater |
| Profit factor | >= 1.5 | greater |
| Trade count | >= 100 | greater |
| Walk-forward years | >= 3 | greater |
| Gate freshness | <= 30 days old | less |

### Inspect the current gate

```bash
python cli.py check-gate
```

## Operational monitoring

### Health check

```bash
# Prints JSON to stdout; exit 0 = healthy, 1 = degraded.
python cli.py health-check

# EC2 / load-balancer one-liner:
python cli.py health-check && echo "Agent OK" || echo "Agent DEGRADED"
```

Output fields:

| Field | Description |
| --- | --- |
| `status` | `"ok"` or `"degraded"` |
| `snapshot_age_seconds` | Seconds since the last snapshot write by the agent |
| `halted` | `true` if any guardrail or manual halt is active |
| `last_tick_age` | Seconds since the last market tick was received |
| `reasons` | List of degraded reasons (only present when `status == "degraded"`) |

The snapshot-staleness check (`> 60 s`) only fires during NSE market hours (Mon-Fri 09:15-15:30 IST) — outside hours an old snapshot is expected and does not degrade the status.

### Live-mode safety

When `mode: live` in config, `cli.py run` calls `is_live_allowed()` before connecting to the broker. If the gate is missing, failed, or stale, startup aborts with a non-zero exit code. There is no override flag — the only path to live is a passing gate.

### Backtest engine integrity

The engine reuses Phase 1 components verbatim — `Guardrails`, `position_size`, `RegimeClassifier`, the three `IStrategy` implementations, and `PaperBroker`. There is no parallel "backtest version" of any of these. If a guardrail rejects an order in production, it would also reject it in backtest, and vice versa.

The engine deliberately uses **conservative fill assumptions**:

- Signals fire on bar D-1's close, fill at bar D's open + slippage
- If both stop and target are within bar D's H/L, the stop is assumed to hit first (worst case)
- No look-ahead: the regime classifier and strategies only see history `[..., D-1]` when deciding for D

## Roadmap

| Phase | Status | Description |
| --- | --- | --- |
| 1 | done | Skeleton, paper broker, live data, guardrails, strategies, orchestrator, tests |
| 2 | done | Backtest engine + walk-forward harness + strict gate (Sharpe > 1.2, MaxDD < 15%) |
| 6 | done | MCP server wrapper with 10 read-only tools |
| 3 | done | News sentiment scorer (Claude) + options PCR/OI feed + FII/DII institutional flows |
| 4 | done | EOD reviewer (Claude Opus 4.7) producing parameter-adjustment proposals |
| 5 | done | Streamlit dashboard + Telegram alerts + email EOD report |
| 6b | done | MCP mutating tools (place_paper_order, halt_agent) via file-based command-queue |
| 7 | done (stub) | Upstox feed stub + AngelOne live broker scaffold; guards in place |
| 8 | future | Live deployment with small capital, after backtest gate + paper proof |

## Safety

- The agent will **refuse to start in `live` mode** until the backtest gate passes (Phase 2 gate logic).
- All hard limits are enforced in [core/risk/guardrails.py](core/risk/guardrails.py); they cannot be bypassed by strategies.
- Every guardrail rejection is logged to SQLite and to `logs/events.jsonl` for audit.
- The paper broker **always** attaches a stop-loss to every position and force-exits when hit.
- Daily-loss and drawdown circuits halt new entries automatically; recovery requires manual unhalt.

## Project layout

```text
core/
  broker/        # Paper broker + IBroker interface + angelone_live stub
  data/          # Angel One feed, tick aggregator, historical fetcher, universe,
                 # FII/DII flows, VIX, PCR, economic calendar, Upstox feed stub
  regime/        # Regime classifier (ADX + BB width + VIX)
  strategies/    # Three baseline strategies + IStrategy interface
  risk/          # Position sizing + guardrails (the safety-critical module)
  execution/     # Orchestrator (the main loop) + file-based command queue
  persistence/   # SQLite store
  llm/           # EOD Claude reviewer + news sentiment scorer
  notifications/ # Telegram notifier + email notifier
  config.py      # Pydantic settings loaded from YAML + .env
  types.py       # Domain types (Tick, Candle, Signal, Order, Position, Regime)
backtest/        # Backtest engine, walk-forward harness, strict gate
mcp_server/      # Read-only MCP server (10 tools)
dashboard.py     # Streamlit dashboard
scripts/         # EC2 startup scripts (.bat / .ps1)
docs/            # ARCHITECTURE.md, LIVE_BROKER_SETUP.md
config/          # YAML config
tests/           # Pytest suite — 348 tests, guardrails covered exhaustively
cli.py           # Entry points: run | warmup | check-config | mcp-server | backtest | walk-forward | check-gate
```
