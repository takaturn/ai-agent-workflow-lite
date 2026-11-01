#!/usr/bin/env bash
# Root calls this only AFTER independently verifying the result and focused checks.
set -euo pipefail
umask 077
die() { printf 'finish-agent: %s\n' "$*" >&2; exit 2; }
failed=0
if [[ ${1:-} == --failed ]]; then failed=1; shift; fi
[[ $# == 2 ]] || die 'usage: [--failed] <summary.json> <short Root verification or diagnosis note>'
[[ -n $2 && ${#2} -le 2000 ]] || die 'verification note must be 1-2000 characters, no secrets'
summary=$(realpath -e -- "$1") || die 'summary missing'
[[ -f $summary && ${summary##*/} == summary.json ]] || die 'invalid summary path'
base=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
caller=$(bash "$base/herdr-context.sh") || exit 2
parent=$(jq -er '.parent_pane_id | select(type=="string" and length>0)' "$summary") || die 'parent missing'
[[ $caller == "$parent" ]] || die 'parent mismatch'
if (( failed )); then
  # A failed run is resolved by Root's diagnosis, not by accepting a result.
  jq -e '.status=="failed" or .status=="interrupted" or .status=="prelaunch_error"' "$summary" >/dev/null || die 'not a failed run'
  jq -e '.root_acceptance=="pending" or .root_acceptance=="accepted"' "$summary" >/dev/null || die 'unexpected acceptance state'
  pane=$(jq -r '.pane_id // ""' "$summary")
  [[ $pane != "$parent" ]] || die 'child equals parent'
  cwd=$(jq -er '.worktree | select(type=="string" and startswith("/"))' "$summary") || die 'cwd missing'
  layout=$(herdr pane layout --pane "$parent") || die 'cannot read layout'
  jq -e --arg parent "$parent" 'any(.result.layout.panes[]; .pane_id==$parent)' >/dev/null <<<"$layout" || die 'parent absent from layout'
  if [[ -n $pane ]] && jq -e --arg pane "$pane" 'any(.result.layout.panes[]; .pane_id==$pane)' >/dev/null <<<"$layout"; then
    record=$(herdr pane get "$pane") || die 'child pane unreadable; retain for diagnosis'
    jq -e --arg pane "$pane" --arg cwd "$cwd" '.result.pane.pane_id==$pane and .result.pane.cwd==$cwd' \
      >/dev/null <<<"$record" || die 'identity mismatch'
    herdr pane close "$pane" || printf 'finish-agent: close reply failed; checking layout\n' >&2
    layout=$(herdr pane layout --pane "$parent") || die 'cleanup unverified; cannot read layout'
    jq -e --arg pane "$pane" 'all(.result.layout.panes[]; .pane_id!=$pane)' >/dev/null <<<"$layout" || die 'cleanup unverified; child still visible'
  fi
  temporary=$(mktemp "$(dirname "$summary")/.accept.XXXXXXXX") || die 'cannot create resolution checkpoint'
  jq --arg note "$2" --arg parent "$parent" \
    '.root_acceptance="accepted" | .accepted_by=$parent | .acceptance_note=$note |
      .failure_resolution=$note | .cleanup="closed"' "$summary" >"$temporary" \
    || { rm -f -- "$temporary"; die 'cannot record resolution'; }
  mv -- "$temporary" "$summary" || die 'cannot commit resolution'
  printf 'Root recorded failed-run resolution and cleanup: summary=%s\n' "$summary"
  exit 0
fi
pane=$(jq -er '.pane_id | select(type=="string" and length>0)' "$summary") || die 'child missing'
[[ $pane != "$parent" ]] || die 'child equals parent'
cwd=$(jq -er '.worktree | select(type=="string" and startswith("/"))' "$summary") || die 'cwd missing'
name=$(jq -er '.agent_name | select(type=="string" and length>0)' "$summary") || die 'agent name missing'
run_id=$(jq -er '.run_id | select(type=="string" and startswith("run."))' "$summary") || die 'run ID missing'
[[ $(basename "$(dirname "$summary")") == "$run_id" ]] || die 'run ID/path mismatch'
jq -e '.status=="returned" and .exit_code==0 and .phase=="result_collected"' "$summary" >/dev/null || die 'not a returned result; retain for diagnosis'
layout_ok() {
  local layout
  layout=$(herdr pane layout --pane "$parent") || return 1
  jq -e --arg parent "$parent" --arg pane "$pane" \
    'any(.result.layout.panes[]; .pane_id==$parent) and all(.result.layout.panes[]; .pane_id!=$pane)' \
    >/dev/null <<<"$layout"
}
update() {
  local cleanup=$1 attempted=${3:-keep} temporary
  temporary=$(mktemp "$(dirname "$summary")/.accept.XXXXXXXX") || die 'cannot create acceptance checkpoint'
  if ! jq --arg note "$2" --arg parent "$parent" --arg cleanup "$cleanup" --arg attempted "$attempted" \
    '.root_acceptance="accepted" | .accepted_by=$parent | .acceptance_note=$note | .cleanup=$cleanup |
      .close_attempted=(if $attempted=="attempt" then true else (.close_attempted // false) end)' \
    "$summary" >"$temporary"; then
    rm -f -- "$temporary"
    die 'cannot record acceptance/cleanup'
  fi
  mv -- "$temporary" "$summary" || die 'cannot commit acceptance checkpoint'
}
if jq -e '.root_acceptance=="accepted" and .cleanup=="closed"' "$summary" >/dev/null; then
  layout_ok || die 'recorded cleanup does not match layout'
  printf 'Root acceptance and cleanup already verified: %s\n' "$run_id"
  exit 0
fi
jq -e '(.root_acceptance=="pending" or .root_acceptance=="accepted") and .cleanup=="retained"' "$summary" >/dev/null || die 'unexpected acceptance/cleanup state'
note=$2
if jq -e '.root_acceptance=="accepted"' "$summary" >/dev/null; then
  jq -e --arg parent "$parent" '.accepted_by==$parent and (.acceptance_note | type=="string" and length>0)' "$summary" >/dev/null || die 'acceptance owner missing or mismatched'
  note=$(jq -r '.acceptance_note' "$summary")
  layout=$(herdr pane layout --pane "$parent") || die 'cleanup unverified; cannot read layout'
  jq -e --arg parent "$parent" 'any(.result.layout.panes[]; .pane_id==$parent)' >/dev/null <<<"$layout" || die 'cleanup unverified; parent absent'
  if jq -e --arg pane "$pane" 'all(.result.layout.panes[]; .pane_id!=$pane)' >/dev/null <<<"$layout"; then
    update closed "$note"
    printf 'Recorded verified prior cleanup without resending close: %s\n' "$run_id"
    exit 0
  fi
  jq -e '.close_attempted!=true' "$summary" >/dev/null || die 'close already attempted; child still visible, inspect manually without resending'
fi
record=$(herdr agent get "$pane") || die 'child agent unreadable; retain for diagnosis'
jq -e --arg pane "$pane" --arg cwd "$cwd" --arg name "$name" \
  '.result.agent.pane_id==$pane and .result.agent.cwd==$cwd and .result.agent.name==$name' \
  >/dev/null <<<"$record" || die 'identity mismatch'
jq -e '.result.agent.agent_status=="idle" or .result.agent.agent_status=="done"' \
  >/dev/null <<<"$record" || die 'child not settled; retain for diagnosis'
# Acceptance is a Root attestation, not a claim that this script verified artifacts.
update retained "$note" attempt
# Never retry a close: a lost reply can still mean it was applied.
herdr pane close "$pane" || printf 'finish-agent: close reply failed; checking layout\n' >&2
layout_ok || die 'cleanup unverified; accepted result retained in summary'
update closed "$note"
printf 'Root accepted and cleanup verified: run_id=%s pane=%s summary=%s\n' "$run_id" "$pane" "$summary"
