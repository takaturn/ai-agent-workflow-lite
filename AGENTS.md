# Lightweight agent workflow

- Work directly unless another agent adds value. To delegate, read `.agents/skills/orchestrate/SKILL.md`.
- Use Herdr for observable child execution. Never fake its environment, attach to unrelated sessions, or silently switch providers.
- `.agents/` paths are **not** a reason by themselves to select the highest model tier. Assess actual difficulty and risk.
- The user's agreed task scope authorizes ordinary edits, focused tests, and valid review corrections without asking again. Do not bypass platform prompts or perform destructive work, privilege changes, scope expansion, external access, or PR merges without appropriate approval.
- Shell/CI runs deterministic checks; AI review is conditional, not mandatory.
- PR text and GitHub review replies are normally Japanese.
- For PR feedback fixes, replies, and thread resolution, read `.agents/skills/address-pr-feedback/SKILL.md`. Treat push, reply, and resolve as separate steps and verify each before reporting completion.
- Runtime data is stored outside Git; record design decisions and measured experiments in Wiki, not in agent context.
