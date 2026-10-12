---
name: orchestrate
description: Automatically split independent deliverables into at most two isolated Herdr Workers, using the least expensive adequate models.
---

# Orchestrate v0

The purpose is productive development, **not** a new orchestration framework. Root owns the task and final acceptance.

## Routing

1. Enumerate deliverables, allowed files, acceptance criteria, dependencies and shared writes. For multiple independent deliverables, automatically apply this Skill and make parallel delegation the first choice, without requiring an explicit user instruction. Check shared CI, authentication, external services and cross-worker diff dependencies, not just different filenames. Resolve conflicts first. For dependent, trivial or costly-to-split work, stay serial and state the reason in one sentence.
2. Classify difficulty and risk independently. Default to `standard`; use `light` for narrow docs/config edits and simple reviews. Use `deep` only for work that actually needs complex reasoning or risk-sensitive changes to authorization, security, concurrency, privilege, or data integrity.
3. A path like `.agents/` does **not** automatically cause deep routing. Assess what changed, not where it lives.
4. Pick `codex` or `claude-only` from the environment's approved providers. The model mapping is in `config/models.json`. Never silently fall back to another provider.
5. Normal flow: at most two isolated implementers, Root's Shell/CI checks, optional independent reviewer. No AI verifier for deterministic checks. Delegation never expands the already authorized task scope.

## Delegate with Herdr

First validate the actual current pane:

```bash
bash scripts/herdr-context.sh
```

If no real managed pane is available, do not fake the environment, attach to another session, or create an unapproved fallback agent. Inform the user of the precise blocker; Root may continue safe work that needs no delegation.

Write a **short**, secret-free task file with objective, allowed scope, acceptance criteria, and explicit stop conditions. State explicitly whether each allowed file is an existing edit or a permitted new file; a missing file must not leave the Worker guessing creation authority. Then run:

```bash
bash scripts/run-agent.sh implement codex light /path/to/task.txt
# Optional independent review, only if justified:
bash scripts/run-agent.sh review codex standard /path/to/review-task.txt
```

The runner checks the parent and new child pane, launches **one** agent, makes one bounded wait, and prints at most 120 recent lines. It keeps the pane for inspection. A successful CLI return means only that an agent returned, **not** that the task was completed correctly. Root verifies actual changes and focused tests.

For two independent deliverables, prepare and compare both assignments serially using the README procedure, including all already active assignments. Write one short task file per Worker with its exact cwd and allowed files. Then run from the owning repository:

```bash
bash scripts/run-pair.sh codex worker-a 51 /absolute/issue-51-worker-a light /absolute/task-a.txt worker-b 52 /absolute/issue-52-worker-b standard /absolute/task-b.txt
```

The pair runner preflights both clean worktrees before launching, reuses `run-agent.sh`, and prefixes each result line with Worker/Issue. Each runner's explicit returned Pane ID and external summary identify its result. Observe those same IDs with `herdr agent get` and bounded `agent read`; do not infer identity from completion order. Either failure makes the pair fail, but the peer is still awaited and neither pane nor worktree is automatically destroyed. No retry. Root validates each diff/test independently before integrating; no automatic merge.

## Execution policy

- Continue authorized in-scope fixes, tests, and valid review findings without repeated human questions.
- Stop at meaningful approval boundaries: destructive commands, major scope/spec changes, privilege escalation, blocked tools, access outside approved workspace, or PR merge.
- Respect the user's Codex/Claude permissions and company policies. Never use another process or provider to circumvent a denial.
- At most two Writers, one per working tree. No infinite retry loops, automatic pane cleanup, or recursive agent spawning.
- Before assigning a writer, follow the README's Worker branch/worktree procedure. Root prepares assignments serially with native Git, runs `scripts/check-worker-worktree.sh`, and compares worker ID, issue, branch and canonical absolute path against every active assignment. Give the child that exact cwd and restrict edits to it; never assign two writers to the same worktree. This is edit isolation, not an access-control sandbox. Do not force branch reuse or clean up another worker's worktree on failure. A pair invocation does not detect assignments owned by another Root: do not launch competing Roots.
- On failure, do not re-send the same implementation blindly; retain the pane and summarize the failure.
- Startup blocked/timeout does not prove the process exited. Re-read the returned Pane ID; after the user resolves its trust/approval UI, only resume when it is idle/done and no original prompt was delivered. Never auto-answer trust prompts. `send-keys esc` can interrupt a Codex turn while its background shell command remains alive; verify the terminal separately and do not call this full process cancellation. Additional instructions after a settled turn are distinct from mid-turn steering.
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
