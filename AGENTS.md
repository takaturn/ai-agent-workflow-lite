# Lightweight agent workflow

- Identify deliverables, acceptance criteria, dependencies, and shared writes for each request. For multiple independent deliverables, automatically read `.agents/skills/orchestrate/SKILL.md` and prefer parallel delegation to at most two Workers in separate worktrees and Herdr panes; no explicit skill invocation is needed. Resolve dependencies and shared file/state, CI, authentication, or external-service conflicts before parallel work. For dependent, trivial, or costly-to-split work, work serially and state the reason in one sentence. Delegation stays within the already authorized scope.
- Use Herdr for observable child execution. Never fake its environment, attach to unrelated sessions, or silently switch providers.
- `.agents/` paths are **not** a reason by themselves to select the highest model tier. Assess actual difficulty and risk.
- The user's agreed task scope authorizes ordinary edits, focused tests, and valid review corrections without asking again. Do not bypass platform prompts or perform destructive work, privilege changes, scope expansion, external access, or PR merges without appropriate approval.
- Shell/CI runs deterministic checks; AI review is conditional, not mandatory.
- Distinguish user-provided information, tool-observed evidence, and inference. Claim verification only when tool evidence establishes the claimed outcome for the current target; command success alone is insufficient. State unperformed checks and uncertainty.
- Before consequential changes or external writes, confirm scope against existing authorization; request approval only if insufficient. Read back the exact affected target after each external write before reporting success.
- Require independent adversarial review for high-impact decisions, including changes to agent authority, launch rules, or evidence standards. Keep static review and executed tests distinct.
- PR text and GitHub review replies are normally Japanese.
- For PR creation or Development link repair on an existing numbered PR, automatically read `.agents/skills/github-pr-creation/SKILL.md`; no explicit skill invocation is needed. Follow its direct-Issue linking and read-back verification rules. Loading the skill does not expand authorization for commit, push, merge, or Issue closure.
- For PR feedback fixes, replies, and thread resolution, read `.agents/skills/address-pr-feedback/SKILL.md`. Treat push, reply, and resolve as separate steps and verify each before reporting completion.
- Runtime data is stored outside Git; record design decisions and measured experiments in Wiki, not in agent context.
- After a Reviewer returns findings, only Root in the main session must read `.agents/skills/review-findings/SKILL.md`, decide FIX/SKIP/STOP within existing authorization, and record every decision; ask the user only for genuinely missing business decisions or permissions. Do not load this Skill in child agents or change Reviewer launch controls.
