# Report template

```markdown
## Scope
- task / period (UTC) / providers / roles
- rule_revision: <repo>@<sha> (span) …  | unknown (why)

## Evidence coverage
- ctx: newest indexed session per provider, sessions in period, pending/failed/stale relevant to the provider
- source access: original reachable | index-derived only
- gaps: <span> — reason   (a gap is not "no problem")
- index refreshed: no | yes (permission, provider)

## Findings
- id / check (P3 | P4 | control)
- verdict: detected | not_detected | unknown
- role / role evidence (kind + source)
- rule: <path>@<revision> — passage
- evidence events: <session/event id>, <UTC time>
- observed facts: minimal summary
- interpretation: comparison with the rule (kept separate from facts)
- unknowns / gaps

## Measurements
- only observed values; "not obtained" otherwise. Root and child separate; launches vs. model use separate.

## Not done
- checks not performed, extra AI evaluation needed (scope, budget)
```
