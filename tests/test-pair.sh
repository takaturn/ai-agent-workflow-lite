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
printf 'PASS: concurrent launch, attributed output, peer failure isolation, duplicate refusal\n'
