---
name: project-gate-failure
description: Backtest gate failed on 3-year walk-forward — agent fired only 9 trades. Root cause is over-restrictive entry conditions, not poor strategy
metadata:
  type: project
---

Walk-forward backtest run on 2026-05-13 against 3 years of Nifty 50 daily Bhavcopy data:

```text
W1 (2023-05-14 -> 2024-05-14):  7 trades, Sharpe 1.33, MaxDD 4.8%, win 71%, pf 12.2  ← strong
W2 (2024-05-14 -> 2025-05-14):  1 trade,  Sharpe -6.78, MaxDD 1.9%, win 0%
W3 (2025-05-14 -> 2026-05-13):  1 trade,  Sharpe -3.76, MaxDD 3.7%, win 0%
Aggregate: 9 trades, Sharpe -0.25, MaxDD 4.82%, win 56%, pf 4.04
```

Gate verdict: FAILED. Failed checks: `sharpe` (-0.25 < 1.2), `n_trades` (9 < 100). Other checks (MaxDD, win rate, profit factor) all passed comfortably.

**Critical insight:** When the agent trades, it's profitable (win rate 56%, profit factor 4.0, drawdown only 4.8%). The strategies and guardrails are NOT the problem.

**The problem is the agent almost never enters.** 9 trades over 3 years × 50 stocks = ~3 trades/year/universe. That's far too restrictive.

**Why:** Likely root causes (in priority order):
1. Regime classifier too strict: ADX > 25 + BB width thresholds filter most days into UNKNOWN, so no strategy is even evaluated.
2. ATR stop multiplier (2.0×) trips per-trade-risk guardrail too often. With ₹1000 capital and 1% risk budget, qty rounds to 0 → signal generated but rejected pre-broker.
3. Donchian period 20 days only fires on the cleanest breakouts; many real trends don't clear that filter.
4. Conservative fill model: signal fires on D-1 close, fills at D's open + slippage. Gap-up opens push stop above open → engine rejects pre-guardrails (`stop_above_open_after_gap` rejection bucket).

**Why the gate is right to fail:** sample size of 9 trades is too small to validate. Even though headline metrics look fine, statistical confidence is near zero.

**How to apply:**
- DO NOT loosen guardrails to make gate pass. Tune entry conditions or add more strategies instead.
- Recommended next step: write a `cli.py debug-rejections` command that re-runs the backtest and prints the full rejection breakdown (which guardrail rule, regime distribution, qty=0 frequency). Tells us exactly what to tune.
- If after tuning we still don't reach 100 trades on Nifty 50 daily bars, options: (a) move to 5-min bars (more entries per stock), (b) widen the universe to F&O (~190 names), (c) accept that the current strategy mix is too conservative and add complementary strategies (gap fade, opening range breakout, MACD crossover).
- Increasing capital from ₹1000 to a realistic figure like ₹5,00,000 will resolve cause #2 (qty=0 rejections) immediately. Recommend doing this for the gate run regardless of the user's actual paper-capital intent.

See [[project-nse-trading-agent]].
