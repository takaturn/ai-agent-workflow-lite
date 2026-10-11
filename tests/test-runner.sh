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
    jq -n --arg status "${TEST_AGENT_STATUS:-idle}" \
      '{result:{agent:{agent_status:$status,tokens:{input_tokens:"120",output_tokens:"30"}}}}' ;;
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
jq -e '.status == "returned" and .tier == "light" and .pane_id == "child" and .input_tokens == 120 and .output_tokens == 30' "$summary" >/dev/null
[[ -z "$(git -C "$tmp/work" status --porcelain)" ]] || { echo 'FAIL: repo dirtied' >&2; exit 1; }

# Herdr reports an unseen completion as done, which is also ready for input.
(cd "$tmp/work" && TEST_AGENT_STATUS=done \
  bash "$base/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >"$tmp/done-output")
grep -q '^result: done' "$tmp/done-output"

# Non-ready states must not reach the successful result read.
for agent_status in working blocked unknown; do
  if (cd "$tmp/work" && TEST_AGENT_STATUS="$agent_status" \
    bash "$base/scripts/run-agent.sh" implement codex light "$tmp/task.txt" \
    >"$tmp/refused-output" 2>"$tmp/refused-error"); then
    echo "FAIL: $agent_status agent accepted" >&2; exit 1
  fi
  grep -q "agent is $agent_status" "$tmp/refused-error"
  [[ ! -s "$tmp/refused-output" ]] || { echo 'FAIL: non-ready result read' >&2; exit 1; }
done

# A malicious/misconfigured XDG_STATE_HOME must not write to the source tree.
if (cd "$tmp/work" && XDG_STATE_HOME="$tmp/work/state" \
  bash "$base/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >/dev/null 2>&1); then
  echo 'FAIL: in-repo state accepted' >&2; exit 1
fi
[[ ! -e "$tmp/work/state" ]] || { echo 'FAIL: rejected state path created files' >&2; exit 1; }
printf 'PASS: context, delegation, bounded output, external state, refusal\n'
