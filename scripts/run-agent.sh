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
[[ ${LAUNCH_TASK_ID:-} =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$ ]] || die 'LAUNCH_TASK_ID must be an explicit stable task ID'
[[ -z ${LAUNCH_PATH:-} ]] || die 'LAUNCH_PATH is unsupported; all invocations use run-agent'
command -v jq >/dev/null 2>&1 || die 'jq not found'
command -v sha256sum >/dev/null 2>&1 || die 'sha256sum not found'
command -v realpath >/dev/null 2>&1 || die 'realpath not found'
command -v python3 >/dev/null 2>&1 || die 'python3 not found'

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
run_id=${run_dir##*/}
# Hash the loaded text used in the prompt, not a second read of a mutable file.
task_sha256=$(printf '%s' "$task" | sha256sum)
task_sha256=${task_sha256%% *}
started="$(date +%s)"
status=running
phase=initialized
child_pane=
reservation_id=
launch_attempted=0
input_tokens=null
output_tokens=null
save_summary() {
  local code=$1 elapsed
  elapsed=$(( $(date +%s) - started ))
  jq -n --arg role "$role" --arg profile "$profile" --arg tier "$tier" \
    --arg model "$model" --arg pane "$child_pane" --arg agent_name "$agent_name" --arg status "$status" \
    --arg run_id "$run_id" --arg parent "$parent_pane" --arg cwd "$root" \
    --arg task_sha256 "$task_sha256" --arg phase "$phase" \
    --arg task_id "$LAUNCH_TASK_ID" --arg reservation_id "$reservation_id" \
    --argjson exit_code "$code" --argjson duration_seconds "$elapsed" \
    --argjson input_tokens "$input_tokens" --argjson output_tokens "$output_tokens" \
    '{role:$role,profile:$profile,tier:$tier,model:$model,pane_id:$pane,agent_name:$agent_name,
      run_id:$run_id,parent_pane_id:$parent,worktree:$cwd,task_sha256:$task_sha256,
      task_id:$task_id,reservation_id:$reservation_id,
      phase:$phase,root_acceptance:"pending",cleanup:"retained",close_attempted:false,
      status:$status,exit_code:$exit_code,duration_seconds:$duration_seconds,
      input_tokens:$input_tokens,output_tokens:$output_tokens}' >"$run_dir/.summary.tmp" || return 1
  mv -- "$run_dir/.summary.tmp" "$run_dir/summary.json"
}
checkpoint() { save_summary null || die 'cannot record run checkpoint'; }
finish() {
  local rc=$?
  trap - EXIT
  if [[ $rc != 0 && $status != interrupted ]]; then
    if [[ $launch_attempted == 1 ]]; then status=failed; else status=prelaunch_error; fi
  fi
  if [[ -n $reservation_id ]]; then
    python3 "$script_dir/launch-gate.py" --task-id "$LAUNCH_TASK_ID" --worktree "$root" \
      finish --reservation-id "$reservation_id" --outcome "$status" || {
        printf 'run-agent: reservation outcome write failed; reservation remains counted\n' >&2
        rc=2
      }
  fi
  save_summary "$rc" || { printf 'run-agent: final summary write failed\n' >&2; exit 2; }
  printf 'Local summary: %s\n' "$run_dir/summary.json" >&2
  exit "$rc"
}
trap finish EXIT
trap 'status=interrupted; exit 130' INT
trap 'status=interrupted; exit 143' TERM
checkpoint
printf 'Run started: run_id=%s parent=%s agent=%s cwd=%s summary=%s\n' \
  "$run_id" "$parent_pane" "$agent_name" "$root" "$run_dir/summary.json" >&2

# Reserve before any child launch side effect. A crash leaves this attempt counted.
reservation_id="$(python3 "$script_dir/launch-gate.py" --task-id "$LAUNCH_TASK_ID" --worktree "$root" \
  reserve --provider "$profile" --model "$model" --path run-agent --run-id "$run_id")" \
  || die 'launch reservation denied'
checkpoint

# Require Herdr's explicit return ID; never infer it from arbitrary layout differences.
split="$(herdr pane split --current --direction right --cwd "$root" --no-focus)" || die 'child pane creation failed'
child_pane="$(jq -er '.result.pane.pane_id' <<<"$split")" || die 'Herdr did not return a child id'
[[ "$child_pane" != "$parent_pane" ]] || die 'child unexpectedly equals parent'
child_record="$(herdr pane get "$child_pane")" || die 'child pane inspection failed'
jq -e --arg id "$child_pane" --arg cwd "$root" \
  '.result.pane.pane_id == $id and .result.pane.cwd == $cwd' \
  >/dev/null <<<"$child_record" || die 'child pane identity/cwd mismatch'
printf 'Agent target: name=%s pane=%s cwd=%s\n' "$agent_name" "$child_pane" "$root" >&2
phase=pane_created
checkpoint

if [[ "$profile" == codex ]]; then
  launch_attempted=1
  sandbox=read-only
  [[ "$role" == implement ]] && sandbox=workspace-write
  args=(--sandbox "$sandbox" -c 'approval_policy="never"' --model "$model")
  [[ -z "$effort" ]] || args+=(-c "model_reasoning_effort=$effort")
  herdr agent start "$agent_name" --kind codex --pane "$child_pane" \
    --timeout 120000 -- "${args[@]}" || die 'Codex start failed; inspect pane'
else
  launch_attempted=1
  permission_mode=plan
  [[ "$role" == implement ]] && permission_mode=acceptEdits
  herdr agent start "$agent_name" --kind claude --pane "$child_pane" \
    --timeout 120000 -- --model "$model" --permission-mode "$permission_mode" \
    --safe-mode || die 'Claude start failed; inspect pane'
fi
phase=agent_ready
checkpoint

instructions="$task

Stay within approved repository scope. Respect CLI and OS permissions; never circumvent denials or run destructive commands without approval. Do not spawn more agents. Finish with:
result: done | blocked | needs-review
changed: short file list or none
checks: commands with pass/fail or not-run
risks: remaining risks or none
next: one next action or none"
phase=prompt_wait
checkpoint
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
# Both seen (idle) and unseen (done) completions are ready for input in Herdr.
case "$agent_status" in
  idle|done) ;;
  *) die "agent is $agent_status, not idle/done; inspect pane" ;;
esac
herdr agent read "$agent_name" --source recent-unwrapped --lines 120 || die 'bounded result read failed'
phase=result_collected
status=returned
checkpoint
printf 'Agent returned; Pane %s retained for inspection (Root still must verify the work).\n' "$child_pane" >&2
