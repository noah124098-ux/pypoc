# pypoc — User Guide

## 1. What the Agent Does

pypoc is an automated paper-trading agent for NSE Nifty 50 stocks. It runs continuously during Indian market hours (09:15–15:30 IST), receives live price data from Angel One SmartAPI, classifies the current market environment, selects the strategy best suited for that environment, and places simulated (paper) orders that go through a hard risk-guardrails layer before execution.

No real money is moved. Every trade is recorded in a local SQLite database and visible in the Streamlit dashboard.

Capital starts at INR 5,00,000. The agent manages up to 5 open positions at once, risking at most 1% of equity per trade.

---

## 2. Dashboard Overview

Start the dashboard: `python cli.py run` then in a separate terminal `streamlit run dashboard.py` (or `scripts\start_dashboard.bat`). Open `http://localhost:8501` in your browser.

**Equity Curve tab** — Line chart of portfolio value from inception. Shows realised P&L day by day. A flat line means no trades fired (common when regime is UNKNOWN or circuits are open).

**Positions tab** — Active open positions with entry price, current price, unrealised P&L, stop-loss level, and strategy name. Each row also shows which regime was in effect when the trade was entered.

**Signals tab** — Recent strategy signals (both executed and guardrail-rejected). A "REJECTED" signal means a guardrail fired; the reason column explains why (e.g., `daily_loss_circuit`, `max_open_positions`).

**Regime Timeline tab** — Bar chart of regime classification over time. Each candle shows whether the market was TREND, RANGE, VOLATILE, or UNKNOWN. Use this to understand why certain strategies fired on certain days.

**Trades tab** — Completed trade history with entry/exit prices, holding period, P&L, and exit reason (stop hit, target hit, intraday square-off, or manual).

**Analytics tab** — Aggregate statistics: win rate, average R-multiple, profit factor, Sharpe, max drawdown. Refreshes after market close each day.

---

## 3. How to Start Paper Trading

**Prerequisites**
- Python 3.11+ installed, virtualenv activated (`.venv\Scripts\Activate.ps1` on Windows)
- Angel One SmartAPI credentials in `.env` (copy `.env.example` and fill in `ANGEL_API_KEY`, `ANGEL_CLIENT_ID`, `ANGEL_PASSWORD`, `ANGEL_TOTP_SECRET`)
- Optional: Telegram bot token + chat ID for mobile alerts

**Step-by-step**

1. Check your config and credentials:
   ```
   python cli.py check-config
   ```
   All items should show OK. Fix any MISSING entries before proceeding.

2. Run the pre-flight check:
   ```
   python cli.py preflight
   ```
   This validates 10 points: config, DB schema, universe list, data feed reachability, guardrail sanity, and more. All must pass.

3. Start the agent:
   ```
   python cli.py run
   ```
   Or use the startup script: `scripts\start_agent.bat`

4. In a second terminal, start the dashboard:
   ```
   streamlit run dashboard.py
   ```

5. Monitor: `http://localhost:8501` for the dashboard, or watch `logs\agent.log` for real-time logs.

To stop gracefully, press Ctrl+C in the agent terminal. Positions are squared off at 15:15 IST automatically each day.

---

## 4. Understanding the Backtest Gate

Before running live or paper trading seriously, the agent validates itself against three years of historical data. This is called the "gate check".

**Run the gate:**
```
python cli.py walk-forward --years 3
python cli.py check-gate
```

**What the gate requires:**

| Metric | Required | What it means |
|---|---|---|
| Sharpe ratio | >= 1.2 | Risk-adjusted return must be strong |
| Max drawdown | <= 15% | Worst losing streak must be contained |
| Win rate | >= 45% | At least 45% of trades must be profitable |
| Profit factor | >= 1.5 | Total wins must be 1.5x total losses |
| Trade count | >= 100 | Enough trades to be statistically meaningful |
| Walk-forward period | 3 years | Must cover multiple market cycles |
| File age | <= 30 days | Gate result must be fresh |

**Current status:** Gate is failing (Sharpe ~0.32). The agent runs in paper-trade mode regardless, but switching to live trading is blocked until the gate passes. This is a safety feature — never remove it.

**Why it fails now:** The 2025–2026 window is a correction-and-recovery market. Long-only trend strategies underperform in this environment. Work is in progress to tune strategy parameters.

---

## 5. Understanding Signals and Trades

A **signal** is generated when a strategy's entry conditions are satisfied. It then passes through 14 guardrail checks. If all pass, a paper order is placed.

**What you see in the Positions tab for each open trade:**
- Symbol (e.g., RELIANCE, INFY)
- Side: LONG or SHORT
- Entry price and current market price
- Stop-loss price (mandatory for every trade — orders without a stop are rejected)
- Target price (where the strategy expects to take profit)
- Unrealised P&L in INR
- Strategy that generated the signal
- Regime at entry

**Exit reasons you will see in the Trades tab:**
- `stop_hit` — price touched the stop-loss level
- `target_hit` — price reached the profit target
- `intraday_squareoff` — 15:15 IST forced close (all intraday positions close daily)
- `drawdown_circuit` — portfolio-level drawdown circuit fired mid-session
- `manual` — operator-initiated close via MCP tool

---

## 6. How Regime Classification Works

Every time a Nifty 50 candle closes, the regime classifier evaluates three indicators and assigns a regime label. Strategies only enter trades that match their allowed regime.

**VOLATILE** — India VIX is above 20. Market is fearful or in rapid flux. Only the `volatility_compression` strategy runs (trades narrow-range breakout patterns).

**TREND** — Nifty ADX (14-period) is above 20. Price is moving directionally. The `trend_breakout`, `ema_crossover`, and `rsi_momentum` strategies run.

**RANGE** — Bollinger Band width (20-period, 2 std) is below 6% of price. Market is consolidating sideways. The `mean_reversion` strategy runs (buys oversold dips, sells overbought bounces).

**UNKNOWN** — None of the above conditions met. No new entries are placed until regime becomes clear.

The rationale for each classification is logged and visible in the Regime Timeline tab. Example log: `ADX 22.4 > 20 → TREND`.

---

## 7. Risk Management

Every order passes through 14 guardrail checks before execution. There are no overrides.

**Per-trade limits**
- Maximum 1% of equity at risk per trade (position size is calculated from this)
- Maximum 20% of equity in any single position
- Stop-loss is mandatory — no stop, no trade

**Portfolio-level circuits**

| Circuit | Trigger | Effect |
|---|---|---|
| Daily loss circuit | Day P&L < -3% of equity | No new entries for the rest of the session |
| Drawdown circuit | Peak-to-trough drawdown > 10% | Agent halts; EOD review triggered |
| Black swan — Nifty move | Nifty moves > 2% in 15 minutes | New entries halted immediately |
| Black swan — VIX jump | VIX spikes > 20% in one reading | New entries halted immediately |
| Stale data | No ticks for > 10 seconds during market hours | New entries blocked until feed recovers |

**After a halt**, the agent resumes automatically at the next session start (09:15 next trading day) unless a drawdown circuit fired, in which case an EOD review must clear it first. You can also resume manually via the MCP tool: `python cli.py mcp-server` and then call `resume_agent`.

**Other guardrails checked per order:**
- Liquidity: order quantity cannot exceed 1% of average daily volume
- Spread: bid-ask spread must be below 0.3%
- Signal cooldown: same symbol cannot re-enter within 30 minutes of an exit
- Duplicate position: no double-entry in the same symbol

---

## 8. EOD Review (AI Analysis)

After market close each day, the agent calls Claude (Opus model) with a structured summary of the day's trades, P&L by strategy, current regime, and portfolio statistics.

The reviewer produces:
- A plain-English assessment of the day's performance
- Parameter-adjustment proposals (e.g., "tighten RSI oversold threshold to 30 in current regime")
- Risk flags if drawdown is accelerating

Proposals are logged to `logs/eod_review.log` and shown in the dashboard Analytics tab. They are **not applied automatically** (the `auto_apply_within_safe_bounds` config is `false` by default). A human must review and apply any changes via `config/default.yaml`.

---

## 9. Telegram Alerts

Enable Telegram alerts by setting `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`, then set `telegram_enabled: true` in `config/default.yaml`.

**Alert types and what they mean:**

| Alert | Meaning |
|---|---|
| Agent Started | Agent came online. Shows mode (paper/live) and starting capital. |
| Agent Stopped | Clean shutdown. Shows reason (end-of-day, Ctrl+C, etc.). |
| AGENT HALTED | A circuit breaker fired. Shows which circuit and the trigger value. Investigate immediately. |
| BUY / SELL trade alert | A trade was executed. Shows symbol, strategy, stop-loss, target, and P&L on exit. |
| EOD Summary | End-of-day recap: equity, day P&L, trade count, closing regime. |
| Regime Change | Market environment shifted (e.g., RANGE → TREND). Strategy selection changes accordingly. |
| Weekly Gate | Weekly walk-forward result. PASSED means the agent's backtest metrics are healthy. FAILED means the gate requires attention before enabling live trading. |

---

## 10. Troubleshooting

**Agent starts but places no trades**
- Check the regime: if UNKNOWN, no strategy is active. View the Regime Timeline tab.
- Check if a circuit is open: `python cli.py status` shows circuit state.
- Check guardrail rejections in the Signals tab. A reason like `daily_loss_circuit` means -3% was hit earlier.

**"preflight failed: feed unreachable"**
- Angel One credentials may be stale. TOTP rotates; check `.env` values.
- Run `python cli.py check-config` to validate each credential field.

**Dashboard shows no data / blank charts**
- The agent must be running (or have run previously) to populate `data/agent.db`.
- If the DB is missing, run `python cli.py warmup` to initialise the schema.

**Gate check fails with "file too old"**
- Re-run the walk-forward: `python cli.py walk-forward --years 3`
- This takes 5–10 minutes. The result is written to `data/backtest_gate.json`.

**Logs fill up disk**
- Run `scripts\rotate_logs.bat` manually, or ensure the scheduled task is active (runs daily at 06:05 per `scripts\start_agent.bat` setup).

**Agent halted mid-session (drawdown circuit)**
- Review the EOD analysis in the dashboard Analytics tab.
- If you want to resume the same day (not recommended): `python cli.py mcp-server` → call `resume_agent` via an MCP client.
- Otherwise the agent restarts automatically next trading day.

**"RuntimeError: ...DATA-ONLY..."**
- A code path tried to place a real Angel One order through the data feed credentials. This is a hard security block — it must not be bypassed. File an issue and review the offending code path.
