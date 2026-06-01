/**
 * FULL-CI WORKFLOW
 *
 * Complete CI pipeline: lint, test, backtest gate, push.
 * Run this before any PR or deployment.
 *
 * Usage:
 *   Workflow({ name: "full-ci" })
 */
export const meta = {
  name: 'full-ci',
  description: 'Full CI: tests + gate check + lint + push',
  whenToUse: 'Before merging, deploying, or after a batch of worker changes.',
  phases: [
    { title: 'Test', detail: 'Run full pytest suite' },
    { title: 'Gate', detail: 'Run walk-forward gate check' },
    { title: 'Push', detail: 'Commit any unstaged changes, push to remote' },
  ],
}

phase('Test')

const [testResult, lintResult] = await parallel([
  () => agent(`Run the full test suite.
Working directory: C:\\Users\\Administrator\\pypoc
Run: .venv/Scripts/python.exe -m pytest -v 2>&1 | tail -40
Return JSON: { passed: number, failed: number, all_passed: boolean, output: string, failing_tests: array }`, {
    label: 'ci:pytest', phase: 'Test',
    schema: { type: 'object', properties: { passed: { type: 'number' }, failed: { type: 'number' }, all_passed: { type: 'boolean' }, output: { type: 'string' }, failing_tests: { type: 'array', items: { type: 'string' } } }, required: ['passed', 'failed', 'all_passed', 'output', 'failing_tests'] },
  }),

  () => agent(`Run syntax check on all changed Python files.
Working directory: C:\\Users\\Administrator\\pypoc
Run: git diff HEAD --name-only | grep "\\.py$" | head -20
Then for each file: .venv/Scripts/python.exe -m py_compile <file>
Also: .venv/Scripts/python.exe -m py_compile dashboard.py
Return JSON: { all_ok: boolean, errors: array of strings }`, {
    label: 'ci:lint', phase: 'Test',
    schema: { type: 'object', properties: { all_ok: { type: 'boolean' }, errors: { type: 'array', items: { type: 'string' } } }, required: ['all_ok', 'errors'] },
  }),
])

log(`Tests: ${testResult?.passed} passed, ${testResult?.failed} failed`)

if (!testResult?.all_passed) {
  log(`BLOCKING: ${testResult?.failed} test failures — fix before gate check`)
}

phase('Gate')

const gateResult = await agent(`Run the walk-forward gate check.
Working directory: C:\\Users\\Administrator\\pypoc
Run: .venv/Scripts/python.exe cli.py walk-forward --years 3 --end-date 2026-05-29 2>&1 | tail -25
Also: .venv/Scripts/python.exe cli.py check-gate 2>&1 | tail -10
Return JSON: { gate_passed: boolean, aggregate_sharpe: number, failures: array, output: string }`, {
  label: 'ci:gate', phase: 'Gate',
  schema: { type: 'object', properties: { gate_passed: { type: 'boolean' }, aggregate_sharpe: { type: 'number' }, failures: { type: 'array', items: { type: 'string' } }, output: { type: 'string' } }, required: ['gate_passed', 'aggregate_sharpe', 'failures', 'output'] },
})

phase('Push')

const pushResult = await agent(`Commit any unstaged changes and push.
Working directory: C:\\Users\\Administrator\\pypoc

1. git status --short
2. If there are unstaged changes to tracked files that should be committed:
   - git add -u (tracked files only — no untracked)
   - git commit -m "chore: ci cleanup\\n\\nCo-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
3. git push
4. git log --oneline -5

Return JSON: { had_unstaged: boolean, committed: boolean, pushed: boolean, log: string }`, {
  label: 'ci:push', phase: 'Push',
  schema: { type: 'object', properties: { had_unstaged: { type: 'boolean' }, committed: { type: 'boolean' }, pushed: { type: 'boolean' }, log: { type: 'string' } }, required: ['had_unstaged', 'committed', 'pushed', 'log'] },
})

return {
  tests: testResult,
  lint: lintResult,
  gate: gateResult,
  push: pushResult,
  summary: {
    all_tests_pass: testResult?.all_passed,
    gate_passed: gateResult?.gate_passed,
    aggregate_sharpe: gateResult?.aggregate_sharpe,
    pushed: pushResult?.pushed,
  },
}
