---
name: orchestrate
description: Delegate one focused coding or review task through a verified Herdr pane, using the least expensive adequate model.
---

# Orchestrate v0

The purpose is productive development, **not** a new orchestration framework. Root owns the task and final acceptance.

## Routing

1. For small predictable edits, **do not launch another agent**. Delegate only when independent work or review will help.
2. Classify difficulty and risk independently. Default to `standard`; use `light` for narrow docs/config edits and simple reviews. Use `deep` only for work that actually needs complex reasoning or risk-sensitive changes to authorization, security, concurrency, privilege, or data integrity.
3. A path like `.agents/` does **not** automatically cause deep routing. Assess what changed, not where it lives.
4. Pick `codex` or `claude-only` from the environment's approved providers. The model mapping is in `config/models.json`. Never silently fall back to another provider.
5. Normal flow: one implementer, Shell/CI checks, optional independent reviewer. No AI verifier for deterministic checks.

## Delegate with Herdr

First validate the actual current pane:

```bash
bash scripts/herdr-context.sh
```

If no real managed pane is available, do not fake the environment, attach to another session, or create an unapproved fallback agent. Inform the user of the precise blocker; Root may continue safe work that needs no delegation.

Write a **short**, secret-free task file with objective, allowed scope, acceptance criteria, and explicit stop conditions. Then run:

```bash
bash scripts/run-agent.sh implement codex light /path/to/task.txt
# Optional independent review, only if justified:
bash scripts/run-agent.sh review codex standard /path/to/review-task.txt
```

The runner checks the parent and new child pane, launches **one** agent, makes one bounded wait, and prints at most 120 recent lines. It keeps the pane for inspection. A successful CLI return means only that an agent returned, **not** that the task was completed correctly. Root verifies actual changes and focused tests.

## Execution policy

- Continue authorized in-scope fixes, tests, and valid review findings without repeated human questions.
- Stop at meaningful approval boundaries: destructive commands, major scope/spec changes, privilege escalation, blocked tools, access outside approved workspace, or PR merge.
- Respect the user's Codex/Claude permissions and company policies. Never use another process or provider to circumvent a denial.
- One writer per working tree. v0 has no parallel writers, infinite retry loops, automatic pane cleanup, or recursive agent spawning.
- Before assigning a writer, follow the README's Worker branch/worktree procedure. Root prepares assignments serially with native Git, runs `scripts/check-worker-worktree.sh`, and compares worker ID, issue, branch and canonical absolute path against every active assignment. Give the child that exact cwd and restrict edits to it; never assign two writers to the same worktree. This is edit isolation, not an access-control sandbox. Do not force branch reuse or clean up another worker's worktree on failure. Herdr parallel launch remains outside v0.
- On failure, do not re-send the same implementation blindly; retain the pane and summarize the failure.
- After Root verifies and accepts successful work, close the child pane created for that task. First re-read its identity, cwd, and settled state; never close Root, another task's pane, or a working/blocked agent. Use `herdr pane close <verified-child-id>` and read the parent's layout back to confirm removal. Retain a successful pane only when the user asks; failure panes remain available for diagnosis.

## Child handoff

Ask child agents to end with five compact lines:

```text
result: done | blocked | needs-review
changed: short file list or none
checks: commands plus results or not-run
risks: remaining risks or none
next: one next step or none
```

Root decides acceptance based on code and focused checks, not self-reported completion.

## Retention

Only concise runtime metadata is written under `${XDG_STATE_HOME:-$HOME/.local/state}/ai-agent-workflow-lite/`, never inside the repository. No raw prompt or full agent transcript is automatically saved. Human-facing experiments and design decisions belong in GitHub Wiki.
