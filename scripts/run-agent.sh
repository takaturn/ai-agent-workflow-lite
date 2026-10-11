#!/usr/bin/env bash
set -euo pipefail
umask 077
die() { printf 'run-agent: %s\n' "$1" >&2; exit 2; }

[[ $# == 4 ]] || die 'usage: run-agent.sh <implement|review> <codex|claude-only> <light|standard|deep> <task-file>'
role="$1"; profile="$2"; tier="$3"; task_file="$4"
case "$role" in implement|review) ;; *) die 'invalid role' ;; esac
case "$profile" in codex|claude-only) ;; *) die 'invalid profile' ;; esac
case "$tier" in light|standard|deep) ;; *) die 'invalid tier' ;; esac
[[ -f "$task_file" && -r "$task_file" ]] || die 'task file unreadable'
[[ -s "$task_file" ]] || die 'task file empty'
[[ "$(wc -c <"$task_file")" -le 24000 ]] || die 'task file exceeds 24KB'
command -v jq >/dev/null 2>&1 || die 'jq not found'
command -v sha256sum >/dev/null 2>&1 || die 'sha256sum not found'
command -v realpath >/dev/null 2>&1 || die 'realpath not found'

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
parent_pane="$(bash "$script_dir/herdr-context.sh")" || exit 2
root="$(git rev-parse --show-toplevel 2>/dev/null)" || die 'run in a Git repository'
root="$(cd "$root" && pwd -P)" || die 'repository root is inaccessible'
config="$script_dir/../config/models.json"
model="$(jq -er --arg p "$profile" --arg t "$tier" '.profiles[$p][$t].model // empty' "$config")" || die 'no model configured'
effort="$(jq -er --arg p "$profile" --arg t "$tier" '.profiles[$p][$t].effort // ""' "$config")" || die 'model policy invalid'
task="$(cat -- "$task_file")"

# Resolve and reject in-repository state BEFORE creating any directories.
state_root="${XDG_STATE_HOME:-$HOME/.local/state}"
[[ "$state_root" = /* ]] || die 'XDG_STATE_HOME must be an absolute path'
state_candidate="$(realpath -m -- "$state_root")" || die 'cannot resolve state path'
case "$state_candidate/" in "$root/"*) die 'XDG_STATE_HOME must be outside the working tree' ;; esac
mkdir -p -- "$state_candidate"
state_root="$(cd "$state_candidate" && pwd -P)" || die 'cannot access state directory'
case "$state_root/" in "$root/"*) die 'state directory resolves inside the working tree' ;; esac
repo_id="$(printf '%s' "$root" | sha256sum | cut -c1-16)"
state_base="$state_root/ai-agent-workflow-lite/$repo_id"
mkdir -p -- "$state_base"
run_dir="$(mktemp -d "$state_base/run.XXXXXXXX")" || die 'cannot create run directory'

agent_name="${role}-$(date +%s)-$$"
started="$(date +%s)"
status=failed
child_pane=
input_tokens=null
output_tokens=null
finish() {
  rc=$?
  elapsed=$(( $(date +%s) - started ))
  jq -n --arg role "$role" --arg profile "$profile" --arg tier "$tier" \
    --arg model "$model" --arg pane "$child_pane" --arg status "$status" \
    --argjson exit_code "$rc" --argjson duration_seconds "$elapsed" \
    --argjson input_tokens "$input_tokens" --argjson output_tokens "$output_tokens" \
    '{role:$role,profile:$profile,tier:$tier,model:$model,pane_id:$pane,
      status:$status,exit_code:$exit_code,duration_seconds:$duration_seconds,
      input_tokens:$input_tokens,output_tokens:$output_tokens}' >"$run_dir/summary.json" || true
  printf 'Local summary: %s\n' "$run_dir/summary.json" >&2
}
trap finish EXIT

# Require Herdr's explicit return ID; never infer it from arbitrary layout differences.
split="$(herdr pane split --current --direction right --cwd "$root" --no-focus)" || die 'child pane creation failed'
child_pane="$(jq -er '.result.pane.pane_id' <<<"$split")" || die 'Herdr did not return a child id'
[[ "$child_pane" != "$parent_pane" ]] || die 'child unexpectedly equals parent'
child_record="$(herdr pane get "$child_pane")" || die 'child pane inspection failed'
jq -e --arg id "$child_pane" --arg cwd "$root" \
  '.result.pane.pane_id == $id and .result.pane.cwd == $cwd' \
  >/dev/null <<<"$child_record" || die 'child pane identity/cwd mismatch'

if [[ "$profile" == codex ]]; then
  sandbox=read-only
  [[ "$role" == implement ]] && sandbox=workspace-write
  args=(--sandbox "$sandbox" -c 'approval_policy="never"' --model "$model")
  [[ -z "$effort" ]] || args+=(-c "model_reasoning_effort=$effort")
  herdr agent start "$agent_name" --kind codex --pane "$child_pane" \
    --timeout 120000 -- "${args[@]}" || die 'Codex start failed; inspect pane'
else
  permission_mode=plan
  [[ "$role" == implement ]] && permission_mode=acceptEdits
  herdr agent start "$agent_name" --kind claude --pane "$child_pane" \
    --timeout 120000 -- --model "$model" --permission-mode "$permission_mode" \
    --safe-mode || die 'Claude start failed; inspect pane'
fi

instructions="$task

Stay within approved repository scope. Respect CLI and OS permissions; never circumvent denials or run destructive commands without approval. Do not spawn more agents. Finish with:
result: done | blocked | needs-review
changed: short file list or none
checks: commands with pass/fail or not-run
risks: remaining risks or none
next: one next action or none"
herdr agent prompt "$agent_name" "$instructions" --wait --timeout 600000 \
  >/dev/null || die 'prompt failed or timed out; inspect pane; no automatic retry'

agent_record="$(herdr agent get "$agent_name")" || die 'agent status inspection failed'
agent_status="$(jq -er '.result.agent.agent_status' <<<"$agent_record")" || die 'agent status missing'
# Herdr sometimes returns numeric token counts as strings. Record only valid counters.
input_tokens="$(jq -er '(.result.agent.tokens.input_tokens // empty) |
  if type=="string" and test("^[0-9]+$") then tonumber
  elif type=="number" and . >= 0 then .
  else empty end' <<<"$agent_record" 2>/dev/null)" || input_tokens=null
output_tokens="$(jq -er '(.result.agent.tokens.output_tokens // empty) |
  if type=="string" and test("^[0-9]+$") then tonumber
  elif type=="number" and . >= 0 then .
  else empty end' <<<"$agent_record" 2>/dev/null)" || output_tokens=null
[[ "$agent_status" == idle ]] || die "agent is $agent_status, not idle; inspect pane"
herdr agent read "$agent_name" --source recent-unwrapped --lines 120 || die 'bounded result read failed'
status=returned
printf 'Agent returned; Pane %s retained for inspection (Root still must verify the work).\n' "$child_pane" >&2
