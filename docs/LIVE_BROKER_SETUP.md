# Live Broker Setup Guide

## Prerequisites
1. Backtest gate must pass (Sharpe >= 1.2) — check: python cli.py check-gate
2. Paper trading must show profitability for >= 30 days
3. Create a SEPARATE Angel One API app (never reuse the data-feed app)

## Required Credentials (separate app)
Add to .env (NEVER the same values as ANGEL_ONE_API_KEY):
  ANGEL_ONE_LIVE_API_KEY=<your-live-app-key>
  ANGEL_ONE_LIVE_CLIENT_CODE=<your-client-code>
  ANGEL_ONE_LIVE_PASSWORD=<your-password>
  ANGEL_ONE_LIVE_TOTP_SECRET=<your-totp-secret>

## Why Separate App
The data-feed app has order methods neutralized (raises RuntimeError).
The live-broker app needs a different set of permissions configured in Angel One dashboard.
Mixing the two would either break data feeds or violate the DATA-ONLY constraint.

## How to Enable
1. Set mode: live in config/default.yaml (currently: paper)
2. cli.py run validates backtest gate before allowing live mode
3. AngelOneLiveBroker.from_env() reads ANGEL_ONE_LIVE_* vars

## Safety Constraints (enforced in code)
- All orders still pass through Guardrails.check() — no bypass
- Daily loss circuit (-3%) auto-halts
- Drawdown circuit (-10%) auto-halts
- MCP halt_agent command works in live mode
