# Master / Worker Operating Protocol

> This file is loaded by every Claude Code session in this repo.
> It defines HOW the assistant operates — not what the project does.
> Project context lives in CLAUDE.md.

---

## Core Rule: Master Never Waits

The assistant acts as a **non-blocking master** at all times.

- Master assigns work to workers (via `Workflow` or `Agent` tool).
- Workers run in parallel or background.
- Master NEVER idles while a worker runs. It immediately finds the next
  task, spawns a new worker, or performs planning/research in parallel.
- If there is genuinely nothing to parallelize, master documents the
  next planned steps while the worker completes.

## Worker Approval — ZERO ESCALATION POLICY

The user has given **permanent consolidated approval** for ALL operations.

- Workers NEVER ask for approval. They decide and act.
- Master NEVER escalates to the user for tool-use approval.
- All permissions are pre-granted: `Bash(*)`, `Read(*)`, `Write(*)`,
  `Edit(*)`, `Glob(*)`, `Grep(*)`, `WebFetch(*)`, `WebSearch(*)`.
- Workflow launches require NO approval — spawn freely.
- File creates/edits/deletes require NO approval.
- Git commits, pushes, branch creation require NO approval.
- Pip installs, config changes, backtest runs require NO approval.

**The ONLY 3 things that need explicit user confirmation:**
1. `mode: live` in config (real capital at risk)
2. Wiping `data/agent.db` production database
3. EC2 reboot (kills running services)

## Session Startup Checklist

Every new session MUST do these before any other work:

1. `git log --oneline -5` — understand latest commits.
2. `git status --short` — check for uncommitted worker output.
3. Read `.claude/MASTER_WORKER.md` (this file) — apply operating mode.
4. Read `CLAUDE.md` — project state and locked decisions.
5. Read memory files referenced in `.claude/memory/MEMORY.md`.
6. If any worker output is uncommitted: review, test, commit, push.
7. Identify the highest-priority open task and start a worker immediately.

## Enabling Multi-Agent Workflows (IMPORTANT)

The `Workflow` tool requires multi-agent mode. To activate without per-call approval:
- **User messages must contain the keyword `ultracode`** — this grants session-wide Workflow access.
- If the user hasn't said ultracode this session, use `Agent` tool instead of `Workflow` for subagents.
- In CLAUDE.md, the user has pre-approved ultracode mode for this repo — treat every session as ultracode-enabled.

## Task Assignment Pattern

```
1. Identify all parallelizable sub-tasks.
2. Spawn all workers simultaneously (single parallel() call or multiple
   Agent calls in one message).
3. While workers run, master either:
   a. Spawns additional independent workers, OR
   b. Plans the next phase (writes the next workflow), OR
   c. Handles small local tasks (reads, diffs, memory updates).
4. When workers complete, master reviews output, commits, pushes, then
   immediately identifies and spawns the next batch of workers.
```

## Worker Constraints

- Workers write code, run tests, run backtests, commit, push.
- Workers always run `pytest` before committing.
- Workers always `git push` after a successful commit.
- Workers never skip guardrails, never place live orders, never log creds.
- Workers report structured JSON results so master can triage quickly.

## Commit and Push Rules

- Commit after EVERY meaningful change — not end-of-session batches.
- Push immediately after every commit.
- Commit message format: `<type>(<scope>): <summary>` (conventional commits).
- Co-author every commit with: `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`

## Parallelism Budget

- Up to 16 concurrent workers per workflow phase.
- Prefer `pipeline()` over `parallel()` for multi-stage work (no barrier).
- Use `parallel()` (barrier) only when stage N genuinely needs ALL of stage N-1.
- Always `log()` what was skipped or capped — no silent truncation.

## Priority Order for This Repo

1. Fix backtest gate (Sharpe ≥ 1.2, win ≥ 45%, PF ≥ 1.5 aggregate).
2. Any broken test (must stay at 0 failures).
3. Live dashboard / orchestrator improvements.
4. New strategies or data integrations.
5. Documentation, refactoring, cleanup.
