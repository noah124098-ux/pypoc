/**
 * MASTER ORCHESTRATOR WORKFLOW
 *
 * Reads the current repo state, derives the highest-priority work items,
 * and spawns focused worker sub-agents for each — all in parallel.
 *
 * Usage:
 *   Workflow({ name: "master" })
 *
 * The master workflow is the entry-point for any session that wants full
 * parallel coverage.  It discovers work, fans out, collects results, commits,
 * and pushes — all without blocking.
 */
export const meta = {
  name: 'master',
  description: 'Master orchestrator: discover work, fan out workers, commit, push',
  whenToUse: 'Use at the start of any session to automatically pick up and parallelize all pending work.',
  phases: [
    { title: 'Discover', detail: 'Read repo state, test results, gate status' },
    { title: 'Assign',   detail: 'Spawn parallel workers for each work item' },
    { title: 'Integrate', detail: 'Review results, run tests, commit, push' },
  ],
}

// ── Phase 1: Discover ────────────────────────────────────────────────────────
phase('Discover')

const DISCOVERY_SCHEMA = {
  type: 'object',
  properties: {
    git_status:        { type: 'string' },
    recent_commits:    { type: 'string' },
    test_summary:      { type: 'string' },
    test_failures:     { type: 'array', items: { type: 'string' } },
    gate_status:       { type: 'string' },
    open_issues:       { type: 'array', items: { type: 'string' } },
    priority_tasks:    { type: 'array', items: { type: 'string' } },
  },
  required: ['git_status', 'recent_commits', 'test_summary', 'test_failures', 'open_issues', 'priority_tasks'],
}

const [discovery, gateCheck] = await parallel([
  () => agent(`You are the MASTER discovery agent for the pypoc NSE trading agent project.
Working directory: C:\\Users\\Administrator\\pypoc

Run these commands and report findings:
1. git status --short
2. git log --oneline -6
3. .venv/Scripts/python.exe -m pytest -q 2>&1 | tail -5
4. cat data/backtest_gate.json (just the metrics and failures sections)
5. Read CLAUDE.md lines 1-50 for current open issues

Return structured JSON with git_status, recent_commits, test_summary, test_failures (list of failing test names or []),
open_issues (list from CLAUDE.md), and priority_tasks (your ranked list of what to work on next, max 6 items).`, {
    label: 'discover:state',
    phase: 'Discover',
    schema: DISCOVERY_SCHEMA,
  }),

  () => agent(`Run the walk-forward backtest with pinned end date to get current gate metrics.
Working directory: C:\\Users\\Administrator\\pypoc

Run: .venv/Scripts/python.exe cli.py walk-forward --years 3 --end-date 2026-05-29 2>&1 | tail -22

Parse the output and return JSON:
{
  w1_sharpe: number, w2_sharpe: number, w3_sharpe: number,
  aggregate_sharpe: number, aggregate_win_rate: number,
  aggregate_pf: number, max_dd: number, gate_passed: boolean,
  failures: array of string,
  raw_output: string
}`, {
    label: 'discover:gate',
    phase: 'Discover',
    schema: {
      type: 'object',
      properties: {
        w1_sharpe: { type: 'number' }, w2_sharpe: { type: 'number' }, w3_sharpe: { type: 'number' },
        aggregate_sharpe: { type: 'number' }, aggregate_win_rate: { type: 'number' },
        aggregate_pf: { type: 'number' }, max_dd: { type: 'number' },
        gate_passed: { type: 'boolean' }, failures: { type: 'array', items: { type: 'string' } },
        raw_output: { type: 'string' },
      },
      required: ['aggregate_sharpe', 'gate_passed', 'failures', 'raw_output'],
    },
  }),
])

log(`Discovery complete. Tests: ${discovery?.test_summary}`)
log(`Gate: passed=${gateCheck?.gate_passed}, sharpe=${gateCheck?.aggregate_sharpe}`)
log(`Priority tasks: ${JSON.stringify(discovery?.priority_tasks)}`)

// ── Phase 2: Assign — fan out workers for each priority task ─────────────────
phase('Assign')

const tasks = (discovery?.priority_tasks || []).slice(0, 6)
const testFailures = discovery?.test_failures || []

// Always spawn a test-fix worker if there are failures
const workItems = [
  ...(testFailures.length > 0 ? [`Fix failing tests: ${testFailures.join(', ')}`] : []),
  ...tasks,
]

log(`Spawning ${workItems.length} workers in parallel...`)

const WORKER_SCHEMA = {
  type: 'object',
  properties: {
    task: { type: 'string' },
    success: { type: 'boolean' },
    files_changed: { type: 'array', items: { type: 'string' } },
    committed: { type: 'boolean' },
    pushed: { type: 'boolean' },
    test_result: { type: 'string' },
    summary: { type: 'string' },
    next_steps: { type: 'array', items: { type: 'string' } },
  },
  required: ['task', 'success', 'committed', 'pushed', 'summary'],
}

const workerResults = await parallel(
  workItems.map((task, i) => () => agent(`You are WORKER-${i + 1} for the pypoc NSE trading agent project.
Working directory: C:\\Users\\Administrator\\pypoc

You have been assigned this task by the master:
TASK: ${task}

Operating rules (follow exactly):
1. Read the relevant files before making changes.
2. Make the minimum change that accomplishes the task.
3. Never weaken guardrails, never place live orders, never log credentials.
4. Run .venv/Scripts/python.exe -m pytest -q after changes. Fix any failures before committing.
5. If tests pass, git add the changed files, git commit with a conventional commit message,
   then git push.
6. If you are uncertain about a decision, choose the safer/more conservative option.
   Do NOT escalate to the user — make the call yourself.

Commit message format:
  <type>(<scope>): <summary>
  Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

Return structured JSON with: task, success, files_changed, committed, pushed, test_result, summary, next_steps.`, {
    label: `worker:${i + 1}:${task.slice(0, 30)}`,
    phase: 'Assign',
    schema: WORKER_SCHEMA,
  }))
)

// ── Phase 3: Integrate ────────────────────────────────────────────────────────
phase('Integrate')

const [finalTests, finalGate] = await parallel([
  () => agent(`Run the final test suite and report.
Working directory: C:\\Users\\Administrator\\pypoc
Run: .venv/Scripts/python.exe -m pytest 2>&1 | tail -5
Return JSON: { passed: number, failed: number, all_passed: boolean, output: string }`, {
    label: 'integrate:tests',
    phase: 'Integrate',
    schema: { type: 'object', properties: { passed: { type: 'number' }, failed: { type: 'number' }, all_passed: { type: 'boolean' }, output: { type: 'string' } }, required: ['passed', 'failed', 'all_passed', 'output'] },
  }),

  () => agent(`Run final gate check with pinned date and report delta vs earlier run.
Working directory: C:\\Users\\Administrator\\pypoc
Run: .venv/Scripts/python.exe cli.py walk-forward --years 3 --end-date 2026-05-29 2>&1 | tail -22
Also: git log --oneline -8
Return JSON: { aggregate_sharpe: number, gate_passed: boolean, output: string, commits: string }`, {
    label: 'integrate:gate',
    phase: 'Integrate',
    schema: { type: 'object', properties: { aggregate_sharpe: { type: 'number' }, gate_passed: { type: 'boolean' }, output: { type: 'string' }, commits: { type: 'string' } }, required: ['aggregate_sharpe', 'gate_passed', 'output', 'commits'] },
  }),
])

const succeeded = workerResults.filter(Boolean).filter(r => r.success).length
const failed    = workerResults.filter(Boolean).filter(r => !r.success).length

return {
  discovery,
  gateCheck,
  workerResults,
  finalTests,
  finalGate,
  summary: {
    workers_succeeded: succeeded,
    workers_failed: failed,
    all_tests_pass: finalTests?.all_passed,
    gate_passed: finalGate?.gate_passed,
    aggregate_sharpe: finalGate?.aggregate_sharpe,
  },
}
