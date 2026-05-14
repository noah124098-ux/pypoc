---
name: feedback-collab-style
description: How to collaborate with this user — Q&A before non-trivial work, concise updates, push back when load-bearing, no narration of internal thought
metadata:
  type: feedback
---

**Communication style preferences (observed across many turns):**

1. **Brainstorm first, implement second.** For non-trivial design choices (architecture, dependencies, integration points, security tradeoffs), present 2-4 options via `AskUserQuestion` BEFORE writing code. The user explicitly said "ask me questions let's brainstorm" at the start.

2. **Concise updates only.** No multi-paragraph status reports. One sentence per update is usually enough. End-of-turn summary: 1-2 sentences max.

3. **Don't narrate internal deliberation.** Skip "let me think...", "I'm going to...", "now I'll...". State the action briefly, do it.

4. **Push back when load-bearing.** The user respects firm pushback on:
   - Security risks (leaked credentials, weakened guardrails, bypassing safety checks)
   - Correctness (silent test failures, off-by-one bugs in critical code)
   - Architecture mistakes that cost weeks to fix later
   Do not water down the pushback. Make it actionable: explain *what* the risk is, *why* it matters, and *what* the alternative is.

5. **Confirm before risky/irreversible actions.** The user has explicitly accepted some risks before (e.g. "I want to use the leaked key anyway"). Respect that decision while keeping STRUCTURAL mitigations in place (see [[feedback-angel-one-data-only]] — the user accepted credential-exposure risk but the data-only code guards stay).

6. **Match scope to request.** A bug fix doesn't need surrounding cleanup. A one-shot script doesn't need a class hierarchy. The user has called out "no premature abstraction" implicitly via approval of direct, simple code.

7. **Markdown formatting in answers.** GFM. Use tables for structured comparisons, code blocks with language hints, file links as `[filename.py](path/to/filename.py)`.

8. **The user's stated priorities (verbatim):** "robust", "guardrails", "market driven", "self-decision making", "stop loss mechanism". Every decision should preserve these.

**How to apply:**
- Before any non-trivial implementation, ask 1-4 focused multiple-choice questions via `AskUserQuestion`.
- After completing work, summarize in 1-2 sentences max. Skip the "I just did X, Y, Z" recap unless the user asks.
- When the user pushes back ("don't do that", "stop"), re-derive what they actually want; don't argue.
- When the user accepts a risk you flagged, proceed with their decision, but note any structural mitigation you'll keep.
