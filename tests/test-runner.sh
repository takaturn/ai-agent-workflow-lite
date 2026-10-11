#!/usr/bin/env bash
set -euo pipefail
base="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
command -v jq >/dev/null || { echo 'jq required' >&2; exit 2; }
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/work" "$tmp/state"
git -C "$tmp/work" init -q
cat >"$tmp/bin/herdr" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
case "$1 $2" in
  'pane get')
    jq -n --arg id "$3" --arg cwd "$TEST_ROOT" \
      '{result:{pane:{pane_id:$id,cwd:$cwd,agent_status:"idle"}}}'
    ;;
  'pane layout')
    printf '%s\n' '{"result":{"layout":{"panes":[{"pane_id":"parent"},{"pane_id":"child"}]}}}'
    ;;
  'pane split')
    printf '%s\n' '{"result":{"pane":{"pane_id":"child"}}}' ;;
  'agent start'|'agent prompt')
    exit 0 ;;
  'agent get')
    printf '%s\n' '{"result":{"agent":{"agent_status":"idle"}}}' ;;
  'agent read')
    printf '%s\n' 'result: done' 'changed: none' 'checks: not-run' 'risks: none' 'next: none' ;;
  *)
    echo "Unexpected Herdr operation: $*" >&2; exit 99 ;;
esac
MOCK
chmod +x "$tmp/bin/herdr"
export PATH="$tmp/bin:$PATH" TEST_ROOT="$tmp/work" XDG_STATE_HOME="$tmp/state"
export HERDR_ENV=1 HERDR_PANE_ID=parent

test "$(bash "$base/scripts/herdr-context.sh")" = parent
if HERDR_ENV=0 bash "$base/scripts/herdr-context.sh" >/dev/null 2>&1; then
  echo 'FAIL: missing Herdr context accepted' >&2; exit 1
fi
if HERDR_PANE_ID=stale bash "$base/scripts/herdr-context.sh" >/dev/null 2>&1; then
  echo 'FAIL: pane absent from live layout accepted' >&2; exit 1
fi
if HERDR_PANE_ID='' bash "$base/scripts/herdr-context.sh" >/dev/null 2>&1; then
  echo 'FAIL: missing pane id accepted' >&2; exit 1
fi

printf 'Do nothing and report.\n' >"$tmp/task.txt"
(cd "$tmp/work" && bash "$base/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >"$tmp/output")
grep -q '^result: done' "$tmp/output"
summary="$(find "$tmp/state" -name summary.json -type f | head -n1)"
[[ -n "$summary" ]] || { echo 'FAIL: summary missing' >&2; exit 1; }
jq -e '.status == "returned" and .tier == "light" and .pane_id == "child"' "$summary" >/dev/null
[[ -z "$(git -C "$tmp/work" status --porcelain)" ]] || { echo 'FAIL: repo dirtied' >&2; exit 1; }

# A malicious/misconfigured XDG_STATE_HOME must not write to the source tree.
if (cd "$tmp/work" && XDG_STATE_HOME="$tmp/work/state" \
  bash "$base/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >/dev/null 2>&1); then
  echo 'FAIL: in-repo state accepted' >&2; exit 1
fi
[[ ! -e "$tmp/work/state" ]] || { echo 'FAIL: rejected state path created files' >&2; exit 1; }
printf 'PASS: context, delegation, bounded output, external state, refusal\n'
