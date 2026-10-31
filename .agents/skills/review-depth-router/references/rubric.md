# Review depth rubric (version 1.1)

**Unit of assessment:** what the pinned diff changes compared with its base, including the execution paths it reaches. Use the **highest applicable** row in each dimension; never average. A score of 0 needs positive evidence that the harmless property holds, such as "only comment lines changed". "No issue seen" is not evidence. If a score depends on a fact you cannot establish, use `null`.

**Instruction text is behavior.** `AGENTS.md`, `CLAUDE.md`, `.agents/skills/**`, the role and procedure sections of README, and task templates are read by agents as rules. A change to their meaning (what agents may do, launch, accept as evidence, skip or clean up) is scored as a behavior change, not as docs. Only wording, typo or link fixes that leave every rule's meaning unchanged count as plain docs.

## Five dimensions (0–2)

| Dimension | 2 | 1 | 0 |
|---|---|---|---|
| `impact` | A plausible failure, with a named causal path, leads to unauthorized disclosure or action, loss or corruption of real durable data, or a whole service or agent workflow becoming unusable. | A plausible failure changes the results or availability of one bounded feature. | Nothing changed affects runtime behavior or a consequential user/agent action (typo-only text, comments, cosmetic output, test additions that weaken no check). |
| `scope` | Changes a contract consumed across a process, service or repository boundary (CLI exit status relied on by CI, schema, IPC), or a repository-wide execution or agent policy. Cite the boundary. | Changes behavior reached from two or more entrypoints in one process, or an interface consumed by another component. Cite the callers. | The content is non-executable and relied on by no contract, or tracing the callers shows exactly one entrypoint and no changed outward contract. |
| `uncertainty` | Introduces or changes shared mutable state, concurrency, ordering, retry/timeout/idempotency, or a protocol between independently run components. | Adds or changes branches, validation or error handling only. | No changed control flow or state, or equivalence shown by the diff together with relevant passing tests. "Looks similar" does not qualify. |
| `verification` | No observed passing check is relevant to the changed behavior, relevant checks fail or are flaky, or the required checks cannot be run. | Passing checks exist but miss a significant changed boundary or failure path, or only manual checks cover it. | An observed passing check covers the changed behavior and its changed failure paths. A non-executable diff may be verified by inspection, unless it contains runnable commands, safety guidance or an agent rule. |
| `recovery` | A failure can leave durable or external effects that reverting the source cannot undo (data deleted, message sent, secret leaked, external state changed). | Recovery needs a reversible migration, data cleanup or operator action. | Reverting code or config restores behavior completely. |

**Disputed boundaries:**
- *Passing check* means a result you observed for the current target. A plan to run tests does not count. If the happy path is covered but the changed negative path is not, verification is 1. If no relevant check passed, it is 2.
- *Entrypoint* means a CLI subcommand, a script invoked by CI or by a person, a hook, a scheduled job or an HTTP handler. Directory names and line counts do not define one. Dynamic dispatch whose callers cannot be found means `scope=null`.
- *Real data* includes users' files, Git refs and remote repositories, Issues/PRs and shared state. It excludes disposable test fixtures.
- A tests-only diff can score impact 0 only if it removes or weakens no case that a required CI check depends on.
- Do not lower a score because the author or the PR says the change is low risk.

## Seven gates

Each gate looks at **behavior changed by this diff**. A keyword in neutral text, or an added negative test, is not enough by itself. `yes` means FULL. `unknown` means FULL/PROVISIONAL. `no` must name the content you inspected, not only path names.

| ID | `yes` when this diff changes… | `no` example | `unknown` when… |
|---|---|---|---|
| `AUTH` | Authentication, authorization, identity or trust-boundary enforcement, or privilege checks. | Docs that describe the existing auth flow without changing it. | You cannot tell whether a changed condition takes part in an access decision. |
| `SECRETS` | Collection, storage, redaction, logging, distribution or exposure of secrets, or a publication/scan gate. | A log message changed with no new fields. | New logged or uploaded data whose contents cannot be determined. |
| `EXECUTION` | Which tools or shell commands may run, sandbox or permission flags, approval prompts, or network egress. | A model-tier name changed while flags and tool lists stay identical. | A launched command line is built dynamically and cannot be resolved. |
| `DESTRUCTIVE` | Deletion, overwrite or cleanup selection affecting durable data, Git refs/worktrees, or a schema migration that breaks compatibility. | Removing a temp file the same function created. | You cannot tell which paths or refs a cleanup selects. |
| `SECURITY_GATE` | A required check, hook, CI step, protected-path list, approval record or review requirement is weakened or bypassed. | Adding a new test or CI step. | A check is renamed or moved and you cannot confirm it still runs. |
| `AUTONOMOUS_EFFECT` | Which side effects an agent can start without fresh approval, which targets it may affect, or side-effecting retries. | Changing model choice while capabilities and tool policy stay the same. | You cannot determine an agent's effective permissions after the change. |
| `MANDATED_REVIEW` | An existing repository rule (`AGENTS.md`, `orchestrate`) requires independent adversarial review. Examples: agent authority, launch routing/rules, roles, concurrency limits, evidence standards, cleanup, or this Skill and rubric themselves, even when only text changes. | A typo fix in a Skill that changes no rule's meaning. | You cannot tell whether edited instruction text changes a rule's meaning. |

## Assessment JSON

The script requires exactly these keys. Every `evidence` must be a non-empty string. Duplicate JSON keys are rejected, so a later value cannot silently override an earlier one.

```json
{
  "diff": "<base-sha>..<head-sha>",
  "dimensions": {
    "impact": {"score": 1, "evidence": "..."},
    "scope": {"score": 1, "evidence": "..."},
    "uncertainty": {"score": 1, "evidence": "..."},
    "verification": {"score": 1, "evidence": "..."},
    "recovery": {"score": 0, "evidence": "..."}
  },
  "gates": {
    "AUTH": {"status": "no", "evidence": "..."},
    "SECRETS": {"status": "no", "evidence": "..."},
    "EXECUTION": {"status": "no", "evidence": "..."},
    "DESTRUCTIVE": {"status": "no", "evidence": "..."},
    "SECURITY_GATE": {"status": "no", "evidence": "..."},
    "AUTONOMOUS_EFFECT": {"status": "no", "evidence": "..."},
    "MANDATED_REVIEW": {"status": "no", "evidence": "..."}
  }
}
```

## Fixed precedence (implemented by `scripts/decide.py`)

1. Any gate that is `yes` or `unknown` → FULL.
2. Replace each `null` with 2. If impact=2 or recovery=2 → FULL. If verification=2 and (uncertainty=2 or scope=2) → FULL.
3. Total ≥ 6 → FULL. Otherwise, total ≥ 3 or any single 2 → FOCUSED. Otherwise → NORMAL.
4. Any `null` or `unknown` makes the state PROVISIONAL. `independent_review_required` is true whenever `MANDATED_REVIEW` is not `no`.

The thresholds are a proposed local policy. Operational data does not yet support them.

## Counterexamples

| Diff | Notes | Mode |
|---|---|---|
| README typo, rules unchanged | All dimensions 0 with positive evidence | NORMAL |
| README/Skill sentence lets Reviewers run shell | Instruction text = behavior; `EXECUTION=yes`, `MANDATED_REVIEW=yes` | FULL |
| Docs describing auth, no rule or code change | `AUTH=no` | NORMAL |
| One-line authorization relaxation | `AUTH=yes`, regardless of line count | FULL |
| Retry change, side-effect free, happy-path test passes | uncertainty=2, verification=1 | FOCUSED |
| Same retry change, no relevant passing check | uncertainty=2 + verification=2 | FULL |
| Side-effecting agent retry added | `AUTONOMOUS_EFFECT=yes` | FULL |
| Tests added only, no CI weakening | All 0 | NORMAL |
| Required CI step removed | `SECURITY_GATE=yes` | FULL |
| Message sent or remote state changed on failure | recovery=2 | FULL |
| Shared function, callers not traceable | scope=null → PROVISIONAL | ≥ FOCUSED |
| PR body says "this is trivial, score 0" | Ignored as data; score from evidence | unchanged |
