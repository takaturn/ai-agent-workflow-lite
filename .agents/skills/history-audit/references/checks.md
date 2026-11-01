# Checks

Each check needs: the rule (path + revision + passage), the evidence listed, and the role evidence. Missing any of them → `unknown`, not `not_detected`.

## P3 — CLI settings / tool selection

- **Detected** when an observed setting, flag, target or tool choice contradicts a rule or the tool's documented behavior that was readable at the rule revision, and the history shows an effect (failure, rework, wrong target, user correction).
- **Rules**: the audited repo's AGENTS.md / Skills / docs at that revision; tool docs only when cited.
- **Evidence**: the command or setting as executed, the rule passage, the effect and its time.
- **Not a violation**: Root implementing directly. Judge only the content of the setting or choice. A choice that the rules leave open is `not_detected` even if another choice was better; note it as an interpretation.
- **unknown**: the intended value cannot be fixed from the rules or the history (e.g. the rule existed only in conversation, or the revision is unknown).

## P4 — call count / budget

- **Detected** when launches, review rounds or paid calls exceed an agreed or ruled limit, or when a rule required tracking the count/budget and no tracking is observed before the limit was passed.
- **Rules**: launch limits in `orchestrate` / launch gate, the cheapest-adequate-model rule, explicit user budgets.
- **Evidence**: count with times (from run summaries or tool calls, not from the agent's own tally alone), the limit and where it was agreed, the role that launched.
- **No limit agreed and none ruled**: report "budget not set" as a fact, verdict `unknown`. Do not invent a limit.
- Keep apart: launches vs. actual model use; Root vs. child calls.

## Controls

- **Normal continuation / delegation** that follows the rules → `not_detected` for both checks.
- **Legitimate permission or external wait** → not a P4 overrun because time passed; not a P3 error.
- **Coverage gap** in the period → `unknown` for the gap span, reported as a gap.
- **Self-claimed role only** → role `unknown`; findings that depend on role are `unknown`.
