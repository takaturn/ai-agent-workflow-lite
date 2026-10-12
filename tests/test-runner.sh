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
  'agent start')
    exit "${TEST_START_EXIT:-0}" ;;
  'agent prompt')
    exit "${TEST_PROMPT_EXIT:-0}" ;;
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
(cd "$tmp/work" && bash "$base/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >"$tmp/output" 2>"$tmp/error")
grep -q '^result: done' "$tmp/output"
summary="$(find "$tmp/state" -name summary.json -type f | head -n1)"
[[ -n "$summary" ]] || { echo 'FAIL: summary missing' >&2; exit 1; }
jq -e '.status == "returned" and .tier == "light" and .pane_id == "child" and .input_tokens == 120 and .output_tokens == 30' "$summary" >/dev/null
jq -e '.agent_name | test("^implement-[0-9]+-[0-9]+$")' "$summary" >/dev/null
name=$(jq -r '.agent_name' "$summary")
grep -Fq "Agent target: name=$name pane=child" "$tmp/error"
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

# Identity survives failures before result collection; diagnostics and summary agree.
for failure in start prompt; do
  start_exit=0; prompt_exit=0
  if [[ $failure == start ]]; then start_exit=34; else prompt_exit=35; fi
  if (cd "$tmp/work" && TEST_START_EXIT=$start_exit TEST_PROMPT_EXIT=$prompt_exit \
    bash "$base/scripts/run-agent.sh" implement codex light "$tmp/task.txt" \
    >"$tmp/failed-output" 2>"$tmp/failed-error"); then
    echo "FAIL: $failure failure accepted" >&2; exit 1
  fi
  summary_path=
  while IFS= read -r line; do
    [[ $line != 'Local summary: '* ]] || summary_path=${line#Local summary: }
  done <"$tmp/failed-error"
  [[ -n $summary_path && ! -s "$tmp/failed-output" ]]
  jq -e '.status == "failed" and .exit_code == 2 and .pane_id == "child" and (.agent_name | test("^implement-[0-9]+-[0-9]+$"))' "$summary_path" >/dev/null
  name=$(jq -r '.agent_name' "$summary_path")
  grep -Fq "Agent target: name=$name pane=child" "$tmp/failed-error"
  if [[ $failure == start ]]; then
    grep -q 'Codex start failed' "$tmp/failed-error"
  else
    grep -q 'prompt failed or timed out' "$tmp/failed-error"
  fi
done

# A malicious/misconfigured XDG_STATE_HOME must not write to the source tree.
if (cd "$tmp/work" && XDG_STATE_HOME="$tmp/work/state" \
  bash "$base/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >/dev/null 2>&1); then
  echo 'FAIL: in-repo state accepted' >&2; exit 1
fi
[[ ! -e "$tmp/work/state" ]] || { echo 'FAIL: rejected state path created files' >&2; exit 1; }
printf 'PASS: context, delegation, bounded output, external state, refusal\n'
