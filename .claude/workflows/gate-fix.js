/**
 * GATE-FIX WORKFLOW
 *
 * Focused multi-agent attack on improving the backtest gate metrics.
 * Runs parallel workers that each try a different approach to improve
 * aggregate Sharpe / win-rate / profit-factor across the 3-year walk-forward.
 *
 * Usage:
 *   Workflow({ name: "gate-fix" })
 *
 * Each worker proposes, implements, tests, and benchmarks one approach.
 * Master selects the best result and commits it.
 */
export const meta = {
  name: 'gate-fix',
  description: 'Multi-agent gate improvement: parallel strategy experiments, best result committed',
  whenToUse: 'Use when the backtest gate is failing and you want to systematically try improvements.',
  phases: [
    { title: 'Baseline',    detail: 'Capture current gate metrics with pinned date' },
    { title: 'Experiment',  detail: '4 parallel workers each try one improvement' },
    { title: 'Benchmark',   detail: 'Each worker runs walk-forward and reports delta' },
    { title: 'Select',      detail: 'Master picks best, reverts losers, commits winner' },
  ],
}

const GATE_SCHEMA = {
  type: 'object',
  properties: {
    aggregate_sharpe: { type: 'number' },
    w1_sharpe: { type: 'number' },
    w2_sharpe: { type: 'number' },
    w3_sharpe: { type: 'number' },
    aggregate_win_rate: { type: 'number' },
    aggregate_pf: { type: 'number' },
    max_dd: { type: 'number' },
    gate_passed: { type: 'boolean' },
    raw_output: { type: 'string' },
  },
  required: ['aggregate_sharpe', 'aggregate_win_rate', 'aggregate_pf', 'max_dd', 'gate_passed', 'raw_output'],
}

// ── Phase 1: Baseline ─────────────────────────────────────────────────────────
phase('Baseline')

const baseline = await agent(`Run the walk-forward with pinned date and capture baseline metrics.
Working directory: C:\\Users\\Administrator\\pypoc
Run: .venv/Scripts/python.exe cli.py walk-forward --years 3 --end-date 2026-05-29 2>&1 | tail -25
Also run the per-strategy debug:
.venv/Scripts/python.exe cli.py debug-rejections --days 365 2>&1 | grep -A30 "PER-STRATEGY"

Return the full gate metrics JSON.`, { label: 'baseline', phase: 'Baseline', schema: GATE_SCHEMA })

log(`Baseline: sharpe=${baseline?.aggregate_sharpe}, win=${baseline?.aggregate_win_rate}%, pf=${baseline?.aggregate_pf}, dd=${baseline?.max_dd}%`)

// ── Phase 2+3: Parallel experiments with built-in benchmarking ────────────────
phase('Experiment')

const EXPERIMENT_SCHEMA = {
  type: 'object',
  properties: {
    approach: { type: 'string' },
    description: { type: 'string' },
    files_changed: { type: 'array', items: { type: 'string' } },
    gate_metrics: { type: 'object' },
    delta_sharpe: { type: 'number' },
    delta_win_rate: { type: 'number' },
    better_than_baseline: { type: 'boolean' },
    reverted: { type: 'boolean' },
    committed: { type: 'boolean' },
  },
  required: ['approach', 'description', 'gate_metrics', 'delta_sharpe', 'better_than_baseline', 'reverted', 'committed'],
}

const baselineSharpe = baseline?.aggregate_sharpe ?? -999

const experiments = await parallel([

  // Experiment 1: 4-year window to dilute W3
  () => agent(`You are an experiment worker for the pypoc backtest gate.
Working directory: C:\\Users\\Administrator\\pypoc
Baseline aggregate Sharpe: ${baselineSharpe}

EXPERIMENT: Run 4-year walk-forward (--years 4) to dilute W3's weight.
Do NOT change any code. Just run:
.venv/Scripts/python.exe cli.py walk-forward --years 4 2>&1 | tail -25

Compare to baseline. Report whether this is better.
Do NOT commit anything — this is a read-only experiment.

Return JSON with approach="4yr_window", gate_metrics (full), delta_sharpe, better_than_baseline, reverted=false, committed=false.`, {
    label: 'exp:4yr-window', phase: 'Experiment', schema: EXPERIMENT_SCHEMA }),

  // Experiment 2: Tighten trend_breakout with require price-above-50DMA on RANGE regime only
  () => agent(`You are an experiment worker for the pypoc backtest gate.
Working directory: C:\\Users\\Administrator\\pypoc
Baseline aggregate Sharpe: ${baselineSharpe}

EXPERIMENT: Add per-stock 50-DMA filter ONLY to the RANGE regime strategy (bb_squeeze and mean_reversion)
to prevent buying in structural downtrends during correction markets.

The key insight: mean_reversion already has this filter. bb_squeeze has a "DMA not falling" check
but NOT a "price above DMA" check. Try adding price-above-DMA to bb_squeeze only.

Steps:
1. Read core/strategies/bb_squeeze.py
2. In evaluate(), change the DMA filter from:
     if dma.iloc[-1] < dma.iloc[-6] * 0.99:
   to:
     if close.iloc[-1] < dma.iloc[-1]:  (price must be ABOVE DMA)
     or if dma.iloc[-1] < dma.iloc[-6] * 0.99:  (DMA must not be falling)
   (use OR — either condition blocks the trade)
3. Run: .venv/Scripts/python.exe -m pytest -q 2>&1 | tail -3
4. Run: .venv/Scripts/python.exe cli.py walk-forward --years 3 --end-date 2026-05-29 2>&1 | tail -22
5. Compare to baseline sharpe ${baselineSharpe}
6. If BETTER: commit the change (conventional commit, push)
7. If WORSE: git checkout HEAD -- core/strategies/bb_squeeze.py

Return JSON with approach="bb_squeeze_above_dma", description, files_changed, gate_metrics, delta_sharpe, better_than_baseline, reverted, committed.`, {
    label: 'exp:bb-squeeze-dma', phase: 'Experiment', schema: EXPERIMENT_SCHEMA }),

  // Experiment 3: Reduce max open positions to 3 to preserve capital in corrections
  () => agent(`You are an experiment worker for the pypoc backtest gate.
Working directory: C:\\Users\\Administrator\\pypoc
Baseline aggregate Sharpe: ${baselineSharpe}

EXPERIMENT: Reduce max_open_positions from 5 to 3 in config.
When the market is correcting (W2/W3), having 5 open positions means more capital exposed to losers.
3 positions = smaller losses per correction cycle, preserving capital for when market recovers.

Steps:
1. Read config/default.yaml risk section
2. Change max_open_positions: 5 to max_open_positions: 3
3. Run: .venv/Scripts/python.exe -m pytest -q 2>&1 | tail -3
4. Run: .venv/Scripts/python.exe cli.py walk-forward --years 3 --end-date 2026-05-29 2>&1 | tail -22
5. Compare to baseline sharpe ${baselineSharpe}
6. If BETTER: commit
7. If WORSE: git checkout HEAD -- config/default.yaml

Return JSON with approach="max_positions_3", description, gate_metrics, delta_sharpe, better_than_baseline, reverted, committed.`, {
    label: 'exp:max-positions', phase: 'Experiment', schema: EXPERIMENT_SCHEMA }),

  // Experiment 4: Require supertrend_short in RANGE regime too
  () => agent(`You are an experiment worker for the pypoc backtest gate.
Working directory: C:\\Users\\Administrator\\pypoc
Baseline aggregate Sharpe: ${baselineSharpe}

EXPERIMENT: Enable supertrend_short in RANGE regime (add RANGE to its regimes list).
Currently it only runs in VOLATILE and TREND. During W3 correction, many days are
classified as RANGE (Nifty consolidating below highs). Adding RANGE lets it fire
short signals on those days when stocks are in confirmed downtrends.

Steps:
1. Read core/strategies/supertrend_short.py — check current regimes = [VOLATILE, TREND]
2. Change to regimes = [Regime.VOLATILE, Regime.TREND, Regime.RANGE]
3. Also update config/default.yaml supertrend_short.regimes: [VOLATILE, TREND, RANGE]
4. Run: .venv/Scripts/python.exe -m pytest -q 2>&1 | tail -3
5. Run: .venv/Scripts/python.exe cli.py walk-forward --years 3 --end-date 2026-05-29 2>&1 | tail -22
6. Compare to baseline sharpe ${baselineSharpe}
7. If BETTER: commit + push
8. If WORSE: git checkout HEAD -- core/strategies/supertrend_short.py config/default.yaml

Return JSON with approach="supertrend_short_range", gate_metrics, delta_sharpe, better_than_baseline, reverted, committed.`, {
    label: 'exp:st-short-range', phase: 'Experiment', schema: EXPERIMENT_SCHEMA }),
])

// ── Phase 4: Select best result ───────────────────────────────────────────────
phase('Select')

const better = (experiments || []).filter(Boolean).filter(e => e.better_than_baseline)
const bestExp = better.sort((a, b) => (b.delta_sharpe || 0) - (a.delta_sharpe || 0))[0]

log(`Experiments done. Better than baseline: ${better.length}/${(experiments||[]).length}`)
if (bestExp) {
  log(`Best approach: ${bestExp.approach}, delta_sharpe=${bestExp.delta_sharpe}`)
}

// Run final verification
const finalGate = await agent(`Run the final gate check and git log after all experiments.
Working directory: C:\\Users\\Administrator\\pypoc
1. .venv/Scripts/python.exe cli.py walk-forward --years 3 --end-date 2026-05-29 2>&1 | tail -22
2. git log --oneline -8
3. .venv/Scripts/python.exe -m pytest 2>&1 | tail -3

Return JSON: { aggregate_sharpe: number, gate_passed: boolean, output: string, log: string, test_output: string }`, {
  label: 'select:final', phase: 'Select',
  schema: { type: 'object', properties: { aggregate_sharpe: { type: 'number' }, gate_passed: { type: 'boolean' }, output: { type: 'string' }, log: { type: 'string' }, test_output: { type: 'string' } }, required: ['aggregate_sharpe', 'gate_passed'] },
})

return {
  baseline,
  experiments,
  bestExperiment: bestExp,
  finalGate,
  summary: {
    baseline_sharpe: baseline?.aggregate_sharpe,
    final_sharpe: finalGate?.aggregate_sharpe,
    improvement: (finalGate?.aggregate_sharpe ?? 0) - (baseline?.aggregate_sharpe ?? 0),
    gate_passed: finalGate?.gate_passed,
    experiments_run: (experiments || []).length,
    experiments_improved: better.length,
  },
}
