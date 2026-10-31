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
4. Pick `claude` (default) or `codex` from the environment's approved providers. The model mapping is in `config/models.json`. Never silently fall back to another provider.
5. Follow the README's role policy: at most two isolated Writers and at most one Reader or Reviewer combined (three child agents total, excluding Root). Launch a Reader only for useful independent investigation; use a Reviewer for independent evaluation of changes. Root owns deterministic Shell/CI checks. Delegation never expands the already authorized task scope.
6. Assess review need by behavioral impact, not file extension. Changes to launch routing, authority boundaries, role assignment, concurrency limits, evidence acceptance or cleanup require independent adversarial review before declaring completion, even when only README/Skill text changes. For other changes, briefly record why independent review is needed or omitted. Root may use `review-depth-router` to choose review depth for any change; its `MANDATED_REVIEW` gate maps to this rule, and no routing result relaxes it. Shell tests do not prove policy consistency. After findings, continue authorized corrections, focused checks, re-review and successful-pane cleanup without waiting for another user prompt; stop only at a real approval boundary or blocker and report it. Do not substitute an apology or plan for the next authorized action.

## Delegate with Herdr

First validate the actual current pane:

```bash
bash scripts/herdr-context.sh
```

If no real managed pane is available, do not fake the environment, attach to another session, or create an unapproved fallback agent. Inform the user of the precise blocker; Root may continue safe work that needs no delegation.

Write a **short**, secret-free task file with objective, allowed scope, acceptance criteria, and explicit stop conditions. State explicitly whether each allowed file is an existing edit or a permitted new file; a missing file must not leave the Worker guessing creation authority. Then run:

```bash
bash scripts/run-agent.sh implement claude light /path/to/task.txt
# Optional independent review, only if justified:
bash scripts/run-agent.sh review codex standard /path/to/review-task.txt  # or claude: see Reader/Reviewer rules
```

The runner publishes `run_id`, parent/agent identity, cwd and summary path before creating a Pane, then checkpoints the exact child ID before agent startup. The summary stores the SHA-256 of the once-loaded task text used in the prompt (shell trailing-newline normalization), never its contents; later file edits do not change that digest. It makes one bounded wait and reads at most 120 recent lines. `phase` records the last verified step (`initialized`, `pane_created`, `agent_ready`, `prompt_wait`, `result_collected`); `prompt_wait` means entering the wait call, not proof of delivery. `status` is `running`, `returned` or `failed`. A returned result leaves `root_acceptance=pending` and `cleanup=retained`: CLI success is not artifact acceptance. Failed runs retain their last phase and resources for diagnosis; never blindly resubmit.

After Root independently verifies actual artifacts and focused checks (or accepts a Reader/Reviewer report as that task's artifact), finish the returned run:

```bash
bash scripts/finish-agent.sh /absolute/run.XXXXXXXX/summary.json 'Root verified: short checks and outcome, no secrets'
```

This is an explicit Root attestation, not automatic content verification. The helper requires a finalized returned result, the same verified parent, matching child/name/cwd and idle/done state. It records acceptance and `close_attempted=true` before sending close, then reads the parent's layout back before recording `cleanup=closed`. An unverified close leaves acceptance recorded but cleanup retained; no automatic retry. On explicit re-execution, a prior accepted run with parent present/child absent can converge to closed without resending close or changing its original note. If an attempted close still shows the child, stop for manual diagnosis, even if the prior reply failed. Accepted/closed runs are idempotent only when layout still agrees. Invoke only with trusted summaries created by this task and under the single-Root coordination rule; do not reassign/move the child during finalization. Failed/blocked/unknown or identity-mismatched runs stop and remain available. This helper does not delete branches/worktrees or merge changes.

For two independent deliverables, prepare and compare both assignments serially using the README procedure, including all already active assignments. Write one short task file per Worker with its exact cwd and allowed files. Then run from the owning repository:

```bash
bash scripts/run-pair.sh claude worker-a 51 /absolute/issue-51-worker-a light /absolute/task-a.txt worker-b 52 /absolute/issue-52-worker-b standard /absolute/task-b.txt
```

The pair runner preflights both clean worktrees before launching, reuses `run-agent.sh`, and prefixes each result line with Worker/Issue. Before agent startup, each runner reports `Agent target: name=<name> pane=<id> cwd=<path>`; its external summary retains `agent_name` and `pane_id` on success or failure. Herdr accepts the unique live agent name or its hosting Pane ID: use `herdr agent get <pane-id>` and `herdr agent read <pane-id> --source recent-unwrapped --lines 40`, or the recorded name. Re-read identity/cwd and require the expected agent occupant; a created Pane or recorded startup name does not prove successful agent startup. Do not infer identity from completion order. Runner or output-attribution failure makes the pair fail; output failures are drained rather than deliberately terminating the child, and the peer is still awaited. Neither pane nor worktree is automatically destroyed. No retry. Root validates each diff/test independently before integrating; no automatic merge.

## Execution policy

### Reader handoff and collection

- Scope a Reader to code reading, dependency/spec investigation or root-cause candidates, not implementation or approval of changes. Prefer light for narrow investigation; choose standard when its actual difficulty warrants it. Do not spawn for a simple search or deterministic check.
- Before launch, Root checks all active assignments and enforces the README's shared Reader/Reviewer slot. These are operational limits, not a global lock in the runner. Root prepares a short task specifying purpose, exact cwd, allowed read scope, full target commit SHA, questions, evidence format and stop conditions. No edits, commit/push, external mutations, recursive delegation or write-requiring tests.
- Resolve the target with `git rev-parse --verify '<revision>^{commit}'`. For stable investigation, tell the Reader to inspect tracked content with `git show <SHA>:<path>` and cite `SHA:path:line`. A separate worktree is optional, not required. A Claude Reader cannot run `git show`; for stable investigation, launch it only in a clean worktree whose HEAD equals the target SHA (Root checks `git status --porcelain` is empty and `git rev-parse HEAD`), otherwise it cites `live:` evidence. For approved live-tree investigation, cite `live:<path>:line` separately from commit evidence; record observation time and the content hash of each cited file, plus base SHA and observed diff. Never attribute uncommitted lines to the base SHA. If content changes during observation, discard the affected evidence and report it as unstable. Before adopting a conclusion, Root rechecks each decisive cited passage/hash; changed evidence requires re-investigation or a coordinated stable snapshot, not reuse of the old claim.
- Reuse the existing read-oriented runner: `bash scripts/run-agent.sh review codex light /absolute/reader-task.txt`. State **Reader** in the task; the current CLI, agent name and summary still use `review` because there is no `reader` CLI role. Do not invent a new argument or use `implement` for a Reader. Codex specifies read-only. Claude Readers/Reviewers launch with `--permission-mode dontAsk --safe-mode --restricted --strict-mcp-config --tools Read Glob Grep`: the agent sees only Read/Glob/Grep, so no write, shell, network, MCP or child-launch tool is exposed; settings-file allow rules are ignored; Read/Glob/Grep are confined to the worktree, including through symlinks; project hooks, CLAUDE.md/AGENTS.md, skills and plugins are not loaded, so the task text must carry the prohibitions. Managed settings still apply, and the Claude process itself writes session data under `~/.claude` and calls the model API. The Herdr pane is interactive input: a leading `!` runs a shell command and a leading `/` a slash command outside the tool set. The runner fixes the first prompt line to `Task from Root:`; never start a follow-up prompt with `!` or `/`. Claude Readers cannot run commands such as `git log`; put needed history in the task. These denials were verified on Claude Code 2.1.314 (Issue #24), and the runner refuses Claude Readers/Reviewers on any other version; re-verify before raising it. Never weaken permissions or silently switch providers to resolve this gate.
- The runner returns the dedicated child Pane ID. Root verifies ID/cwd, reads bounded progress/results and asks for concise findings with target SHA and path:line, established facts, hypotheses and unknowns, plus the five-line handoff. The Reader reports `changed: none`; output must not expose secrets. Root checks cited evidence independently; `returned` does not prove a conclusion.
- Readers/Reviewers must not execute commands that generate files/caches or mutate remote state. Check read-command wrappers for implicit cache/config writes; prefer verified non-mutating commands. A denied cache write does not prove that the intended inspection failed or that no other side effect occurred; report the warning and evidence limits separately. If a check needs writes or is denied, report not-run/blocked and let Root perform authorized focused tests. Every initial and subsequent prompt must preserve these prohibitions; a finding or follow-up never promotes a Reader/Reviewer to Writer. They may read the same worktree as a Writer only under the observation rules above; this is not a second Writer assignment.
- After acceptance, use the existing identity/cwd/settled-state check and close only that task's Pane, then verify layout. Retain failed/blocked panes for diagnosis. Do not remove a shared worktree or treat a Reader as a Writer for cleanup.

### Shared execution policy

- Root and assigned Writers continue authorized in-scope fixes, tests, and valid review findings without repeated human questions. This does not grant Readers/Reviewers edit authority; Root handles their findings or assigns an authorized Writer separately.
- Stop at meaningful approval boundaries: destructive commands, major scope/spec changes, privilege escalation, blocked tools, access outside approved workspace, or PR merge.
- Respect the user's Codex/Claude permissions and company policies. Never use another process or provider to circumvent a denial.
- At most two Writers, one per working tree; Reader and Reviewer share one additional slot, for at most three child agents total (Root excluded). Root enforces the limit across its active tasks. No infinite retry loops, automatic pane cleanup, or recursive agent spawning.
- Immediately before each launch and after Root resumes, inspect the actual parent layout and live agents, and reconcile them with all task assignments using returned Pane ID, role, cwd and settled/working/blocked state. Count retained live Readers/Reviewers as occupying their slot until closed; idle is not released. Do not count or control unrelated workspaces as this task's children. If task ownership, a child's role, or the complete assignment inventory is unknown, stop new launches and reconcile first. Assignment and launch belong to one coordinating Root; concurrent Roots are unsupported without explicit coordination, and observation alone is not an atomic global limit.
- Before assigning a writer, follow the README's Worker branch/worktree procedure. Root prepares assignments serially with native Git, runs `scripts/check-worker-worktree.sh`, and compares worker ID, issue, branch and canonical absolute path against every active assignment. Give the child that exact cwd and restrict edits to it; never assign two writers to the same worktree. This is edit isolation, not an access-control sandbox. Do not force branch reuse or clean up another worker's worktree on failure. A pair invocation does not detect assignments owned by another Root: do not launch competing Roots.
- On failure, do not re-send the same implementation blindly; retain the pane and summarize the failure.
- Startup blocked/timeout does not prove the process exited. Re-read the returned Pane ID; after the user resolves its trust/approval UI, only resume when it is idle/done and no original prompt was delivered. Never auto-answer trust prompts. `send-keys esc` can interrupt a Codex turn while its background shell command remains alive; verify the terminal separately and do not call this full process cancellation. Additional instructions after a settled turn are distinct from mid-turn steering.
- After Root verifies successful work, use `finish-agent.sh` for finalized checkpointed returned runs to record acceptance and verified cleanup. For legacy summaries or explicit manual recovery, re-read identity/cwd/settled state, close only the verified task child and read back the parent's layout; never close Root, another task's pane, or a working/blocked agent. Retain a successful pane only when the user asks; failure panes remain available for diagnosis.

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
