# Handoff — laptop session ending 2026-05-14

> Read [CLAUDE.md](CLAUDE.md) and [.claude/memory/](.claude/memory/) first. This file captures only what's NOT already in those — the immediate "what to do next" detail and a few gotchas worth knowing before resuming work on EC2.

## Where we are

- Phase 1, Phase 2, Phase 6 (MCP) are built and tested. 80/80 tests pass.
- Walk-forward gate failed (see [.claude/memory/project_gate_failure.md](.claude/memory/project_gate_failure.md)).
- EC2 dev environment is set up at `C:\Users\Administrator\pypoc`. Repo cloned, venv built, dependencies installed, tests pass.
- This laptop session is **ending**. All future work happens on EC2.

## Immediate next step

**Add a `cli.py debug-rejections` subcommand** that prints why the engine rejected most signals during the last walk-forward run.

The engine already tracks this — `BacktestResult.rejection_breakdown` is a `dict[str, int]` keyed by rule name (e.g. `"per_trade_risk"`, `"max_position_size"`, `"stop_above_open_after_gap"`). Right now it's only printed inline by `cmd_walk_forward` and not persisted, so a re-run is needed to inspect it.

### Suggested approach (not prescriptive)

Two reasonable shapes:

1. **Re-run a quick backtest in debug mode** — single 1-year window, capital bumped to ₹5,00,000 to neutralize qty=0 rejections, print full rejection histogram + signal-count-per-strategy + regime distribution. Fast (~1 min after Bhavcopy cache is warm).

2. **Persist rejection_breakdown to SQLite during walk-forward**, then have `debug-rejections` read from there without re-running. More code, faster re-inspection.

I'd lean toward option 1 — it's a debugging tool, not a production feature. Add it as a function in `backtest/` and wire `cli.py debug-rejections` to call it.

### What the output should answer

When you run `python cli.py debug-rejections`, you should be able to read off:

- For each guardrail rule, how many signals it rejected (top 5 is enough)
- For each strategy, how many signals it generated and how many fired vs were rejected
- Regime distribution across the period (% TREND / RANGE / VOLATILE / UNKNOWN)
- Bonus: capital sensitivity — how the rejection mix changes between ₹1k, ₹5L, ₹50L starting equity

That tells us exactly which knob to turn first. My prior is that `per_trade_risk` (qty=0 because capital too small) and `UNKNOWN` regime (classifier too strict) will dominate, but it could surprise.

## Gotchas worth knowing

### Bhavcopy cache is gitignored

`data/bhavcopy_cache/` is in `.gitignore` (it's hundreds of MB). On EC2 the first walk-forward run will take 5-10 min to download all daily CSVs from `archives.nseindia.com`. Subsequent runs are instant. Don't be alarmed if the first `python cli.py walk-forward` looks idle for a while — it's downloading.

### `data/backtest_gate.json` IS committed

So you'll see yesterday's failed gate result on EC2 immediately. That's intentional — the freshness check (≤30 days) means it counts as the current state until you re-run.

### `.env` is NOT in the repo

The EC2 needs its own `.env` if you want to run the live agent there. For backtests you don't need credentials at all (Bhavcopy is no-creds). For live (`cli.py run`), you'd need a fresh SmartAPI app whitelisted with EC2's elastic IP — see [.claude/memory/project_ec2_deployment.md](.claude/memory/project_ec2_deployment.md).

### DATA-ONLY guard is real

If you ever wire any code that calls Angel One's `placeOrder`, the test `test_order_methods_are_neutralized_after_login` will fail and the runtime will raise `RuntimeError`. This is intentional — see [.claude/memory/feedback_angel_one_data_only.md](.claude/memory/feedback_angel_one_data_only.md). Don't "fix" it by removing the guard.

### Don't weaken guardrails to make the gate pass

The gate failed on `n_trades` and `sharpe`. The fix is to enter MORE trades by tuning entry conditions or adding strategies. **Not** by relaxing the guardrails layer — those exist to keep live capital safe. If a future change reduces guardrail strictness for the sake of backtest results, push back hard.

### `vectorbt` is removed from requirements

Was a placeholder; we wrote a custom engine instead. Don't add it back unless something genuinely needs it.

## Quick reference for the EC2 session

```powershell
# Resume work
cd $env:USERPROFILE\pypoc
git pull
.\.venv\Scripts\Activate.ps1
code .

# In VS Code terminal
claude

# Always-true verifications
pytest -q                                  # must show 80 passed
python cli.py check-config                 # show creds + risk knobs
python cli.py check-gate                   # show last gate state
```

## What I'd build next, if I were continuing

1. `cli.py debug-rejections` — described above
2. Use the output to make ONE targeted tuning change (likely: reduce ADX trend threshold, or shorten Donchian period, or both — but let the data decide)
3. Re-run `python cli.py walk-forward --years 3`
4. If gate still fails on `n_trades`, repeat. If gate fails on `sharpe` only with sufficient `n_trades`, that's a real strategy problem — different conversation.
5. Once gate passes, move to Phase 3 (news/options/FII signals) which often improves win rate AND adds entry diversity.

That's everything I think the next session needs that isn't already documented elsewhere. Good luck.
