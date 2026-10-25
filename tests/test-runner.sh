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
inspect_phase() {
  [[ ${TEST_INSPECT_STATE:-0} == 1 ]] || return 0
  summaries=("$XDG_STATE_HOME"/ai-agent-workflow-lite/*/run.*/summary.json)
  [[ ${#summaries[@]} == 1 && -f ${summaries[0]} ]]
  jq -e --arg phase "$1" '.phase==$phase and .status=="running" and .exit_code==null and .root_acceptance=="pending" and .cleanup=="retained"' "${summaries[0]}" >/dev/null
}
[[ -z ${TEST_ACTIONS:-} ]] || printf '%s %s\n' "$1" "$2" >>"$TEST_ACTIONS"
case "$1 $2" in
  'pane get')
    jq -n --arg id "$3" --arg cwd "$TEST_ROOT" \
      '{result:{pane:{pane_id:$id,cwd:$cwd,agent_status:"idle"}}}'
    ;;
  'pane layout')
    printf '%s\n' '{"result":{"layout":{"panes":[{"pane_id":"parent"},{"pane_id":"child"}]}}}'
    ;;
  'pane split')
    inspect_phase initialized
    ledgers=("$XDG_STATE_HOME"/ai-agent-workflow-lite/launch-gate/*/state.json)
    [[ ${#ledgers[@]} == 1 && -f ${ledgers[0]} ]]
    jq -e '.reservations[-1].status=="reserved"' "${ledgers[0]}" >/dev/null
    [[ ${TEST_SPLIT_EXIT:-0} == 0 ]] || exit "$TEST_SPLIT_EXIT"
    printf '%s\n' '{"result":{"pane":{"pane_id":"child"}}}' ;;
  'agent start')
    inspect_phase pane_created
    exit "${TEST_START_EXIT:-0}" ;;
  'agent prompt')
    inspect_phase prompt_wait
    if [[ ${TEST_PROMPT_SIGNAL:-0} == 1 ]]; then kill -TERM "$PPID"; exit 143; fi
    [[ -z ${TEST_CAPTURE_PROMPT:-} ]] || printf '%s' "$4" >"$TEST_CAPTURE_PROMPT"
    exit "${TEST_PROMPT_EXIT:-0}" ;;
  'agent get')
    jq -n --arg status "${TEST_AGENT_STATUS:-idle}" \
      '{result:{agent:{agent_status:$status,tokens:{input_tokens:"120",output_tokens:"30"}}}}' ;;
  'agent read')
    inspect_phase prompt_wait
    printf '%s\n' 'result: done' 'changed: none' 'checks: not-run' 'risks: none' 'next: none' ;;
  *)
    echo "Unexpected Herdr operation: $*" >&2; exit 99 ;;
esac
MOCK
chmod +x "$tmp/bin/herdr"
export PATH="$tmp/bin:$PATH" TEST_ROOT="$tmp/work" XDG_STATE_HOME="$tmp/state"
export HERDR_ENV=1 HERDR_PANE_ID=parent
export LAUNCH_TASK_ID=runner-fixture
# These are fixture attestations only; no production approval is inferred.
fixture_policy() {
  XDG_STATE_HOME="$1" python3 "$base/.agents/scripts/launch-gate.py" --task-id "$LAUNCH_TASK_ID" \
    --worktree "$tmp/work" \
    approve --approval-ref fixture-only --provider codex --model gpt-6-luna \
    --additional-launches 100 --paths run-agent >/dev/null
}
for state_dir in "$tmp/state" "$tmp/lifecycle" "$tmp/mutated" \
  "$tmp/checkpoint-initialized" "$tmp/checkpoint-pane_created" \
  "$tmp/checkpoint-agent_ready" "$tmp/checkpoint-prompt_wait" \
  "$tmp/checkpoint-result_collected"; do
  fixture_policy "$state_dir"
done

test "$(bash "$base/.agents/scripts/herdr-context.sh")" = parent
if HERDR_ENV=0 bash "$base/.agents/scripts/herdr-context.sh" >/dev/null 2>&1; then
  echo 'FAIL: missing Herdr context accepted' >&2; exit 1
fi
if HERDR_PANE_ID=stale bash "$base/.agents/scripts/herdr-context.sh" >/dev/null 2>&1; then
  echo 'FAIL: pane absent from live layout accepted' >&2; exit 1
fi
if HERDR_PANE_ID='' bash "$base/.agents/scripts/herdr-context.sh" >/dev/null 2>&1; then
  echo 'FAIL: missing pane id accepted' >&2; exit 1
fi

printf 'Do nothing and report.\n' >"$tmp/task.txt"
# Caller-controlled labels cannot select a different reservation route.
if (cd "$tmp/work" && LAUNCH_PATH=run-pair bash "$base/.agents/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >"$tmp/path-output" 2>"$tmp/path-error"); then
  echo 'FAIL: caller-selected route accepted' >&2; exit 1
fi
grep -q 'LAUNCH_PATH is unsupported' "$tmp/path-error"

# Missing approval policy must stop before a child pane is created.
if (cd "$tmp/work" && XDG_STATE_HOME="$tmp/no-policy" TEST_ACTIONS="$tmp/no-policy-actions" \
  bash "$base/.agents/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >/dev/null 2>"$tmp/no-policy-error"); then
  echo 'FAIL: missing launch policy accepted' >&2; exit 1
fi
grep -q 'launch reservation denied' "$tmp/no-policy-error"
if grep -Eq '^(pane split|agent start)$' "$tmp/no-policy-actions"; then
  echo 'FAIL: missing policy launched a child' >&2; exit 1
fi
(cd "$tmp/work" && bash "$base/.agents/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >"$tmp/output" 2>"$tmp/error")
grep -q '^result: done' "$tmp/output"
summary="$(find "$tmp/state" -name summary.json -type f | head -n1)"
[[ -n "$summary" ]] || { echo 'FAIL: summary missing' >&2; exit 1; }
jq -e '.status == "returned" and .tier == "light" and .pane_id == "child" and .input_tokens == 120 and .output_tokens == 30' "$summary" >/dev/null
jq -e '.agent_name | test("^implement-[0-9]+-[0-9]+$")' "$summary" >/dev/null
jq -e '.phase == "result_collected" and .root_acceptance == "pending" and .cleanup == "retained" and .parent_pane_id == "parent" and (.run_id | startswith("run.")) and (.task_sha256 | test("^[a-f0-9]{64}$"))' "$summary" >/dev/null
jq -e '.task_id == "runner-fixture" and (.reservation_id | length > 0)' "$summary" >/dev/null
ledger="$tmp/state/ai-agent-workflow-lite/launch-gate/$LAUNCH_TASK_ID/state.json"
reservation=$(jq -r '.reservation_id' "$summary")
jq -e --arg id "$reservation" '.reservations[] | select(.id==$id) | .status=="returned"' "$ledger" >/dev/null
name=$(jq -r '.agent_name' "$summary")
grep -Fq "Agent target: name=$name pane=child" "$tmp/error"
[[ -z "$(git -C "$tmp/work" status --porcelain)" ]] || { echo 'FAIL: repo dirtied' >&2; exit 1; }

# Herdr reports an unseen completion as done, which is also ready for input.
(cd "$tmp/work" && TEST_AGENT_STATUS=done \
  bash "$base/.agents/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >"$tmp/done-output")
grep -q '^result: done' "$tmp/done-output"

# Intermediate checkpoints must be visible before the next external step.
(cd "$tmp/work" && XDG_STATE_HOME="$tmp/lifecycle" TEST_INSPECT_STATE=1 \
  bash "$base/.agents/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >"$tmp/lifecycle-output" 2>"$tmp/lifecycle-error")
grep -q '^Run started: run_id=' "$tmp/lifecycle-error"
grep -q '^result: done' "$tmp/lifecycle-output"

# A later file edit must not change the digest of the already loaded task.
mktemp() {
  if [[ ${TEST_MUTATE_TASK:-0} == 1 && $* == *'/run.'* ]]; then
    printf 'Changed after load\n' >"$TEST_TASK_FILE"
  fi
  command mktemp "$@"
}
export -f mktemp
(cd "$tmp/work" && XDG_STATE_HOME="$tmp/mutated" TEST_MUTATE_TASK=1 TEST_TASK_FILE="$tmp/task.txt" TEST_CAPTURE_PROMPT="$tmp/captured-prompt" \
  bash "$base/.agents/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >"$tmp/mutated-output" 2>"$tmp/mutated-error")
unset -f mktemp
summary_path=
while IFS= read -r line; do
  [[ $line != 'Local summary: '* ]] || summary_path=${line#Local summary: }
done <"$tmp/mutated-error"
expected_sha=$(printf '%s' 'Do nothing and report.' | sha256sum)
expected_sha=${expected_sha%% *}
grep -q '^Do nothing and report\.' "$tmp/captured-prompt"
jq -e --arg digest "$expected_sha" '.task_sha256==$digest' "$summary_path" >/dev/null

# Fail checkpoint generation at each boundary; no acceptance/cleanup may occur.
jq() {
  if [[ -n ${TEST_FAIL_PHASE:-} && $* == *'run_id:$run_id'* && " $* " == *" --arg phase $TEST_FAIL_PHASE "* ]]; then return 91; fi
  command jq "$@"
}
export -f jq
for phase in initialized pane_created agent_ready prompt_wait result_collected; do
  : >"$tmp/checkpoint-actions"
  if (cd "$tmp/work" && XDG_STATE_HOME="$tmp/checkpoint-$phase" TEST_FAIL_PHASE=$phase TEST_ACTIONS="$tmp/checkpoint-actions" \
    bash "$base/.agents/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >"$tmp/checkpoint-output" 2>"$tmp/checkpoint-error"); then
    echo "FAIL: $phase checkpoint failure accepted" >&2; exit 1
  fi
  grep -q 'cannot record run checkpoint' "$tmp/checkpoint-error"
  if [[ $phase == initialized ]]; then forbidden='pane split';
  elif [[ $phase == pane_created ]]; then forbidden='agent start';
  elif [[ $phase == agent_ready || $phase == prompt_wait ]]; then forbidden='agent prompt';
  else forbidden='pane close'; fi
  if grep -Fqx "$forbidden" "$tmp/checkpoint-actions"; then
    echo "FAIL: $phase checkpoint failure advanced to $forbidden" >&2; exit 1
  fi
done
unset -f jq

# Non-ready states must not reach the successful result read.
for agent_status in working blocked unknown; do
  if (cd "$tmp/work" && TEST_AGENT_STATUS="$agent_status" \
    bash "$base/.agents/scripts/run-agent.sh" implement codex light "$tmp/task.txt" \
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
    bash "$base/.agents/scripts/run-agent.sh" implement codex light "$tmp/task.txt" \
    >"$tmp/failed-output" 2>"$tmp/failed-error"); then
    echo "FAIL: $failure failure accepted" >&2; exit 1
  fi
  summary_path=
  while IFS= read -r line; do
    [[ $line != 'Local summary: '* ]] || summary_path=${line#Local summary: }
  done <"$tmp/failed-error"
  [[ -n $summary_path && ! -s "$tmp/failed-output" ]]
  jq -e '.status == "failed" and .exit_code == 2 and .pane_id == "child" and (.agent_name | test("^implement-[0-9]+-[0-9]+$"))' "$summary_path" >/dev/null
  if [[ $failure == start ]]; then phase=pane_created; else phase=prompt_wait; fi
  jq -e --arg phase "$phase" '.phase==$phase and .root_acceptance=="pending" and .cleanup=="retained"' "$summary_path" >/dev/null
  reservation=$(jq -r '.reservation_id' "$summary_path")
  jq -e --arg id "$reservation" '.reservations[] | select(.id==$id) | .status=="failed"' "$ledger" >/dev/null
  name=$(jq -r '.agent_name' "$summary_path")
  grep -Fq "Agent target: name=$name pane=child" "$tmp/failed-error"
  if [[ $failure == start ]]; then
    grep -q 'Codex start failed' "$tmp/failed-error"
  else
    grep -q 'prompt failed or timed out' "$tmp/failed-error"
  fi
done

# A failed pane split is prelaunch_error; the consumed reservation remains.
if (cd "$tmp/work" && TEST_SPLIT_EXIT=42 \
  bash "$base/.agents/scripts/run-agent.sh" implement codex light "$tmp/task.txt" \
  >"$tmp/prelaunch-output" 2>"$tmp/prelaunch-error"); then
  echo 'FAIL: pane split failure accepted' >&2; exit 1
fi
summary_path=
while IFS= read -r line; do
  [[ $line != 'Local summary: '* ]] || summary_path=${line#Local summary: }
done <"$tmp/prelaunch-error"
jq -e '.status=="prelaunch_error" and .phase=="initialized"' "$summary_path" >/dev/null
reservation=$(jq -r '.reservation_id' "$summary_path")
jq -e --arg id "$reservation" '.reservations[] | select(.id==$id) | .status=="prelaunch_error"' "$ledger" >/dev/null

# TERM during prompt is recorded as interrupted, without refund.
if (cd "$tmp/work" && TEST_PROMPT_SIGNAL=1 \
  bash "$base/.agents/scripts/run-agent.sh" implement codex light "$tmp/task.txt" \
  >"$tmp/interrupted-output" 2>"$tmp/interrupted-error"); then
  echo 'FAIL: interrupted prompt accepted' >&2; exit 1
fi
summary_path=
while IFS= read -r line; do
  [[ $line != 'Local summary: '* ]] || summary_path=${line#Local summary: }
done <"$tmp/interrupted-error"
jq -e '.status=="interrupted" and .phase=="prompt_wait"' "$summary_path" >/dev/null
reservation=$(jq -r '.reservation_id' "$summary_path")
jq -e --arg id "$reservation" '.reservations[] | select(.id==$id) | .status=="interrupted"' "$ledger" >/dev/null

# A malicious/misconfigured XDG_STATE_HOME must not write to the source tree.
if (cd "$tmp/work" && XDG_STATE_HOME="$tmp/work/state" \
  bash "$base/.agents/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >/dev/null 2>&1); then
  echo 'FAIL: in-repo state accepted' >&2; exit 1
fi
[[ ! -e "$tmp/work/state" ]] || { echo 'FAIL: rejected state path created files' >&2; exit 1; }
printf 'PASS: context, delegation, bounded output, external state, refusal\n'
