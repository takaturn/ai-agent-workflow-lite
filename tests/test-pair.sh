#!/usr/bin/env bash
set -euo pipefail
base=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/scripts" "$tmp/a" "$tmp/b"
cp "$base/scripts/run-pair.sh" "$tmp/scripts/"
printf 'task\n' >"$tmp/task"
cat >"$tmp/scripts/herdr-context.sh" <<'SH'
#!/usr/bin/env bash
exit 0
SH
cat >"$tmp/scripts/check-worker-worktree.sh" <<'SH'
#!/usr/bin/env bash
printf '%s\t%s\tfeature/issue%s\t%s\n' "$1" "$2" "$2" "$3"
SH
cat >"$tmp/scripts/run-agent.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
id=$(basename "$PWD")
touch "$TEST_DIR/$id.started"
for ((n=0;n<100;n++)); do
  [[ -e "$TEST_DIR/a.started" && -e "$TEST_DIR/b.started" ]] && break
  sleep .02
done
[[ -e "$TEST_DIR/a.started" && -e "$TEST_DIR/b.started" ]] || exit 90
if [[ ${DELAY_B:-0} == 1 && $id == b ]]; then sleep .3; fi
printf 'result-%s\n' "$id"
printf 'summary-%s\n' "$id" >&2
[[ ${FAIL_A:-0} != 1 || $id != a ]] || exit 17
touch "$TEST_DIR/$id.finished"
SH
export TEST_DIR="$tmp"
run() { bash "$tmp/scripts/run-pair.sh" codex a 51 "$tmp/a" light "$tmp/task" b 52 "$tmp/b" light "$tmp/task"; }
run >"$tmp/out" 2>"$tmp/err"
[[ -f "$tmp/a.finished" && -f "$tmp/b.finished" ]]
# Concurrent output must remain attributable even when completion order changes.
grep -q '^\[a issue=51\] result-a$' "$tmp/out"
grep -q '^\[b issue=52\] result-b$' "$tmp/out"
grep -q '^\[a issue=51\] summary-a$' "$tmp/out"
rm "$tmp/a.finished" "$tmp/b.finished"
if FAIL_A=1 run >"$tmp/out" 2>"$tmp/err"; then exit 1; fi
grep -q 'worker=a issue=51 runner_exit=17' "$tmp/out"
[[ -f "$tmp/b.finished" && ! -f "$tmp/a.finished" ]]
rm "$tmp/a.started" "$tmp/b.started"
if bash "$tmp/scripts/run-pair.sh" codex a 51 "$tmp/a" light "$tmp/task" a 52 "$tmp/b" light "$tmp/task" >"$tmp/out" 2>"$tmp/err"; then exit 1; fi
grep -q 'duplicate assignment' "$tmp/err"
[[ ! -e "$tmp/a.started" && ! -e "$tmp/b.started" ]]
# Fault injection: runner succeeds but attribution output fails.
rm -f "$tmp/a.finished" "$tmp/b.finished"
printf() {
  if [[ $1 == '[%s issue=%s] %s\n' && ${OUTPUT_FAILED_ONCE:-0} == 0 ]]; then
    OUTPUT_FAILED_ONCE=1
    return 67
  fi
  builtin printf "$@"
}
export -f printf
if run >"$tmp/output-failure" 2>"$tmp/output-error"; then
  builtin printf 'FAIL: attribution output failure accepted\n' >&2
  exit 1
fi
unset -f printf
grep -q 'output_exit=67' "$tmp/output-error"
[[ -f "$tmp/a.finished" && -f "$tmp/b.finished" ]]
# A failed final status line must not skip waiting for a still-running peer.
rm -f "$tmp/a.started" "$tmp/b.started" "$tmp/a.finished" "$tmp/b.finished"
printf() {
  if [[ $1 == 'worker=%s issue=%s runner_exit=%s\n' ]]; then return 68; fi
  builtin printf "$@"
}
export -f printf
status_rc=0
DELAY_B=1 run >"$tmp/status-failure" 2>"$tmp/status-error" || status_rc=$?
unset -f printf
if [[ ! -f "$tmp/b.finished" ]]; then
  # Give this fixture's orphaned mock time to finish before its directory is removed.
  sleep .4
  printf 'FAIL: status output failure skipped peer wait\n' >&2
  exit 1
fi
[[ $status_rc != 0 && -f "$tmp/a.finished" ]]
printf 'PASS: concurrent launch, attributed output, peer failure isolation, duplicate refusal, output failure drain/wait\n'
