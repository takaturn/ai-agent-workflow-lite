#!/usr/bin/env bash
# Root prepares and compares all active assignments serially before this command.
set -euo pipefail
umask 077
die() { printf 'run-pair: %s\n' "$*" >&2; exit 2; }
[[ $# == 11 ]] || die 'usage: <profile> <worker-a> <issue-a> <worktree-a> <tier-a> <task-a> <worker-b> <issue-b> <worktree-b> <tier-b> <task-b>'
[[ ${LAUNCH_TASK_ID:-} =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$ ]] || die 'LAUNCH_TASK_ID must be an explicit stable task ID'
profile=$1; shift
case "$profile" in codex|claude-only) ;; *) die 'invalid profile' ;; esac
base=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
bash "$base/herdr-context.sh" >/dev/null
declare -a ids issues paths tiers tasks pids
for i in 0 1; do
  ids[i]=$1 issues[i]=$2 paths[i]=$3 tiers[i]=$4 tasks[i]=$5; shift 5
  assignment=$(bash "$base/check-worker-worktree.sh" "${ids[i]}" "${issues[i]}" "${paths[i]}")
  IFS=$'\t' read -r worker issue branch canonical <<<"$assignment"
  paths[i]=$canonical
  case "${tiers[i]}" in light|standard|deep) ;; *) die 'invalid tier' ;; esac
  tasks[i]=$(realpath -e -- "${tasks[i]}")
  [[ -f ${tasks[i]} && -r ${tasks[i]} && -s ${tasks[i]} ]] || die 'task unreadable or empty'
  [[ $(wc -c <"${tasks[i]}") -le 24000 ]] || die 'task exceeds 24KB'
done
[[ ${ids[0]} != "${ids[1]}" && ${issues[0]} != "${issues[1]}" && ${paths[0]} != "${paths[1]}" ]] || die 'duplicate assignment'
# Report both assignments before any launch; output failure cannot leave a partial pair.
for i in 0 1; do
  printf 'worker=%s issue=%s worktree=%s tier=%s\n' "${ids[i]}" "${issues[i]}" "${paths[i]}" "${tiers[i]}"
done
# Each runner owns only its returned child ID. Never kill the peer or clean worktrees.
for i in 0 1; do
  (
    set +e
    cd "${paths[i]}" || exit 2
    bash "$base/run-agent.sh" implement "$profile" "${tiers[i]}" "${tasks[i]}" 2>&1 |
      {
        output_rc=0
        while IFS= read -r line || [[ -n $line ]]; do
          # Drain even after output failure; do not SIGPIPE the agent runner.
          printf '[%s issue=%s] %s\n' "${ids[i]}" "${issues[i]}" "$line" || output_rc=$?
        done
        exit "$output_rc"
      }
    pipeline_status=("${PIPESTATUS[@]}")
    if [[ ${pipeline_status[1]} != 0 ]]; then
      printf 'worker=%s issue=%s actual_runner_exit=%s output_exit=%s\n' \
        "${ids[i]}" "${issues[i]}" "${pipeline_status[0]}" "${pipeline_status[1]}" >&2
    fi
    [[ ${pipeline_status[0]} == 0 ]] || exit "${pipeline_status[0]}"
    [[ ${pipeline_status[1]} == 0 ]] || exit 2
    exit 0
  ) &
  pids[i]=$!
done
rc=0
for i in 0 1; do
  child_rc=0
  wait "${pids[i]}" || child_rc=$?
  printf 'worker=%s issue=%s runner_exit=%s\n' "${ids[i]}" "${issues[i]}" "$child_rc" || rc=2
  [[ $child_rc == 0 ]] || rc=2
done
exit "$rc"
