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
  jq -e --arg phase "$1" '.phase==$phase and .status=="running" and .exit_code==null and .root_acceptance=="pending" and .cleanup=="retained" and .result_path==null and .result_bytes==null' "${summaries[0]}" >/dev/null
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
    [[ -z ${TEST_CAPTURE_START:-} ]] || printf '%s\n' "$@" >"$TEST_CAPTURE_START"
    # Simulate the child CLI writing its own session record when it starts.
    if [[ -n ${TEST_CODEX_SESSION:-} ]]; then
      mkdir -p "$CODEX_HOME/sessions/2026/11/07"
      now=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
      jq -cn --arg cwd "$TEST_ROOT" --arg ts "$now" '{type:"session_meta",payload:{timestamp:$ts,cwd:$cwd}}' \
        >"$CODEX_HOME/sessions/2026/11/07/rollout-fixture.jsonl"
      jq -cn '{type:"event_msg",payload:{type:"token_count",info:{total_token_usage:{input_tokens:900,cached_input_tokens:400,cache_write_input_tokens:0,output_tokens:50,reasoning_output_tokens:7,total_tokens:950}}}}' \
        >>"$CODEX_HOME/sessions/2026/11/07/rollout-fixture.jsonl"
    fi
    exit "${TEST_START_EXIT:-0}" ;;
  'agent prompt')
    inspect_phase prompt_wait
    if [[ ${TEST_PROMPT_SIGNAL:-0} == 1 ]]; then kill -TERM "$PPID"; exit 143; fi
    [[ -z ${TEST_CAPTURE_PROMPT:-} ]] || printf '%s' "$4" >"$TEST_CAPTURE_PROMPT"
    # Issue #71: the child records the Root prompt and its completed final reply.
    if [[ ${TEST_CODEX_SESSION:-0} == 1 ]]; then
      jq -cn --arg p "$4" '{type:"response_item",payload:{type:"message",role:"user",content:[{type:"input_text",text:$p}]}},
        {type:"event_msg",payload:{type:"task_complete",last_agent_message:"result: done\nnext: from-record"}}' \
        >>"$CODEX_HOME/sessions/2026/11/07/rollout-fixture.jsonl"
    fi
    if [[ ${TEST_CLAUDE_SESSION:-0} == 1 ]]; then
      mkdir -p "$CLAUDE_CONFIG_DIR/projects/r"
      now=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
      jq -cn --arg p "<pasted_content id=\"a1\">
$4
</pasted_content id=\"a1\">" --arg cwd "$TEST_ROOT" --arg ts "$now" \
        '{type:"user",cwd:$cwd,timestamp:$ts,message:{content:$p}},
         {type:"assistant",cwd:$cwd,timestamp:$ts,message:{stop_reason:"end_turn",content:[{type:"text",text:"result: done\nnext: claude-record"}]}}' \
        >"$CLAUDE_CONFIG_DIR/projects/r/child.jsonl"
    fi
    exit "${TEST_PROMPT_EXIT:-0}" ;;
  'agent get')
    jq -n --arg status "${TEST_AGENT_STATUS:-idle}" \
      '{result:{agent:{agent_status:$status,tokens:(if env.TEST_NO_HERDR_TOKENS == "1" then null else {input_tokens:"120",output_tokens:"30"} end)}}}' ;;
  'agent read')
    inspect_phase prompt_wait
    printf '%s\n' 'result: done' 'changed: none' 'checks: not-run' 'risks: none' 'next: none' ;;
  *)
    echo "Unexpected Herdr operation: $*" >&2; exit 99 ;;
esac
MOCK
chmod +x "$tmp/bin/herdr"
cat >"$tmp/bin/claude" <<'MOCK'
#!/usr/bin/env bash
printf '%s (Claude Code)\n' "${TEST_CLAUDE_VERSION:-2.1.314}"
MOCK
chmod +x "$tmp/bin/claude"
export PATH="$tmp/bin:$PATH" TEST_ROOT="$tmp/work" XDG_STATE_HOME="$tmp/state"
# Never read the developer's real CLI session records during tests.
export CODEX_HOME="$tmp/codex-home" CLAUDE_CONFIG_DIR="$tmp/claude-home"
export HERDR_ENV=1 HERDR_PANE_ID=parent
export LAUNCH_TASK_ID=runner-fixture
# These are fixture attestations only; no production approval is inferred.
fixture_policy() {
  XDG_STATE_HOME="$1" python3 "$base/.agents/scripts/launch-gate.py" --task-id "$LAUNCH_TASK_ID" \
    --worktree "$tmp/work" \
    approve --approval-ref fixture-only --provider codex --model gpt-6-luna \
    --additional-launches 100 --paths run-agent >/dev/null
}
claude_policy() {
  local model
  for model in claude-haiku-5-5 claude-sonnet-5-5; do
    XDG_STATE_HOME="$1" python3 "$base/.agents/scripts/launch-gate.py" --task-id "$LAUNCH_TASK_ID" \
      --worktree "$tmp/work" \
      approve --approval-ref "fixture-$model" --provider claude --model "$model" \
      --additional-launches 10 --paths run-agent >/dev/null
  done
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
(cd "$tmp/work" && TEST_ACTIONS="$tmp/actions" bash "$base/.agents/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >"$tmp/output" 2>"$tmp/error")
grep -q '^result: done' "$tmp/output"
summary="$(find "$tmp/state" -name summary.json -type f | head -n1)"
[[ -n "$summary" ]] || { echo 'FAIL: summary missing' >&2; exit 1; }
jq -e '.status == "returned" and .tier == "light" and .pane_id == "child" and .input_tokens == 120 and .output_tokens == 30' "$summary" >/dev/null
jq -e '.agent_name | test("^implement-[0-9]+-[0-9]+$")' "$summary" >/dev/null
jq -e '.phase == "result_collected" and .root_acceptance == "pending" and .cleanup == "retained" and .parent_pane_id == "parent" and (.run_id | startswith("run.")) and (.task_sha256 | test("^[a-f0-9]{64}$"))' "$summary" >/dev/null
jq -e '.task_id == "runner-fixture" and (.reservation_id | length > 0)' "$summary" >/dev/null
# Issue #71: without a matching session record the screen read is the fallback.
jq -e '.result_path == null and .result_bytes == null' "$summary" >/dev/null
grep -q '^agent read' "$tmp/actions" || { echo 'FAIL: fallback screen read missing' >&2; exit 1; }
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

# Claude Writer: model/effort from config, no prompt wait, AGENTS.md loaded.
claude_policy "$tmp/claude"
(cd "$tmp/work" && XDG_STATE_HOME="$tmp/claude" TEST_CAPTURE_START="$tmp/claude-writer-args" TEST_CAPTURE_PROMPT="$tmp/claude-prompt" \
  bash "$base/.agents/scripts/run-agent.sh" implement claude light "$tmp/task.txt" >"$tmp/claude-output" 2>/dev/null)
grep -q '^result: done' "$tmp/claude-output"
args=$(sed -n '/^--$/,$p' "$tmp/claude-writer-args" | tail -n +2 | tr '\n' ' ')
[[ $args == '--model claude-haiku-5-5 --effort low --permission-mode dontAsk --allowedTools '* ]] \
  || { echo "FAIL: claude writer args: $args" >&2; exit 1; }
expected_tools='Read,Glob,Grep,Edit,Write,Bash(bash tests/*),Bash(python3 tests/*),Bash(git status),Bash(git status --short),Bash(git diff),Bash(git diff --stat),Bash(git log --oneline -10)'
[[ $(sed -n '/^--allowedTools$/,$p' "$tmp/claude-writer-args" | tail -n +2 | paste -sd,) == "$expected_tools" ]] \
  || { echo 'FAIL: claude writer allowlist is not the exact expected set' >&2; exit 1; }
grep -q '^Shell is limited to single commands' "$tmp/claude-prompt"
if grep -q 'Shell is limited' "$tmp/captured-prompt"; then
  echo 'FAIL: codex prompt has claude shell note' >&2; exit 1
fi
if grep -Eqx -- '--safe-mode|acceptEdits|bypassPermissions|auto|Bash|Bash\(\*\)' "$tmp/claude-writer-args"; then
  echo 'FAIL: claude writer has safe-mode or broad permissions' >&2; exit 1
fi
# Claude Reviewer gets only read tools.
(cd "$tmp/work" && XDG_STATE_HOME="$tmp/claude" TEST_CAPTURE_START="$tmp/claude-review-args" \
  bash "$base/.agents/scripts/run-agent.sh" review claude standard "$tmp/task.txt" >/dev/null 2>&1)
args=$(sed -n '/^--$/,$p' "$tmp/claude-review-args" | tail -n +2 | tr '\n' ' ')
[[ $args == '--model claude-sonnet-5-5 --effort medium --permission-mode dontAsk --safe-mode --restricted --strict-mcp-config --tools Read Glob Grep ' ]] \
  || { echo "FAIL: claude review args: $args" >&2; exit 1; }
# The pre-rename profile name is rejected, not aliased.
if (cd "$tmp/work" && XDG_STATE_HOME="$tmp/claude" \
  bash "$base/.agents/scripts/run-agent.sh" implement claude-only light "$tmp/task.txt" >/dev/null 2>&1); then
  echo 'FAIL: claude-only profile accepted' >&2; exit 1
fi
# Claude Reader/Reviewer launch does not depend on the CLI version (Issue #30).
claude_policy "$tmp/claude-v"
(cd "$tmp/work" && XDG_STATE_HOME="$tmp/claude-v" TEST_CLAUDE_VERSION=2.1.316 TEST_ACTIONS="$tmp/claude-v-actions" TEST_CAPTURE_START="$tmp/claude-v-args" \
  bash "$base/.agents/scripts/run-agent.sh" review claude standard "$tmp/task.txt" >/dev/null 2>&1) \
  || { echo 'FAIL: other claude version refused a reviewer' >&2; exit 1; }
grep -Eq '^agent start' "$tmp/claude-v-actions" || { echo 'FAIL: reviewer not started on other version' >&2; exit 1; }
args=$(sed -n '/^--$/,$p' "$tmp/claude-v-args" | tail -n +2 | tr '\n' ' ')
[[ $args == '--model claude-sonnet-5-5 --effort medium --permission-mode dontAsk --safe-mode --restricted --strict-mcp-config --tools Read Glob Grep ' ]] \
  || { echo "FAIL: reviewer args on other version: $args" >&2; exit 1; }
# A task starting with ! or / must not reach the interactive input first.
printf '%s\n' '! touch x' >"$tmp/bang.txt"
claude_policy "$tmp/claude-b"
(cd "$tmp/work" && XDG_STATE_HOME="$tmp/claude-b" TEST_CAPTURE_PROMPT="$tmp/bang-prompt" \
  bash "$base/.agents/scripts/run-agent.sh" review claude standard "$tmp/bang.txt" >/dev/null 2>&1)
[[ $(head -n1 "$tmp/bang-prompt") == 'Task from Root:' ]] || { echo 'FAIL: prompt not prefixed' >&2; exit 1; }

# A malicious/misconfigured XDG_STATE_HOME must not write to the source tree.
if (cd "$tmp/work" && XDG_STATE_HOME="$tmp/work/state" \
  bash "$base/.agents/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >/dev/null 2>&1); then
  echo 'FAIL: in-repo state accepted' >&2; exit 1
fi
[[ ! -e "$tmp/work/state" ]] || { echo 'FAIL: rejected state path created files' >&2; exit 1; }

# Issue #43: the same tier resolves per role (claude implement standard is Haiku High).
(cd "$tmp/work" && XDG_STATE_HOME="$tmp/claude" TEST_CAPTURE_START="$tmp/claude-std-writer-args" \
  bash "$base/.agents/scripts/run-agent.sh" implement claude standard "$tmp/task.txt" >/dev/null 2>&1)
args=$(sed -n '/^--$/,$p' "$tmp/claude-std-writer-args" | tail -n +2 | tr '\n' ' ')
[[ $args == '--model claude-haiku-5-5 --effort high --permission-mode dontAsk --allowedTools '* ]] \
  || { echo "FAIL: claude implement standard args: $args" >&2; exit 1; }
XDG_STATE_HOME="$tmp/codex-role" python3 "$base/.agents/scripts/launch-gate.py" --task-id "$LAUNCH_TASK_ID" \
  --worktree "$tmp/work" approve --approval-ref fixture-codex-role --provider codex --model gpt-6.1-sol \
  --additional-launches 1 --paths run-agent >/dev/null
(cd "$tmp/work" && XDG_STATE_HOME="$tmp/codex-role" TEST_CAPTURE_START="$tmp/codex-review-args" \
  bash "$base/.agents/scripts/run-agent.sh" review codex standard "$tmp/task.txt" >/dev/null 2>&1)
args=$(sed -n '/^--$/,$p' "$tmp/codex-review-args" | tail -n +2 | tr '\n' ' ')
[[ $args == '--sandbox read-only -c approval_policy="never" --model gpt-6.1-sol -c model_reasoning_effort=medium ' ]] \
  || { echo "FAIL: codex review standard args: $args" >&2; exit 1; }

# Invalid model policy fails before any reservation or pane, even when the model is approved.
mkdir -p "$tmp/badcfg/.agents/config"
cp -R "$base/.agents/scripts" "$tmp/badcfg/.agents/scripts"
valid='{"schema_version":2,"efforts":{"codex":["low","medium"]},"roles":{"codex":{"implement":{"light":{"model":"gpt-6-luna","effort":"low"}}}}}'
bad_cases=(
  "schema:$(jq -c '.schema_version=1' <<<"$valid")"
  "legacy:$(jq -c '{profiles:{codex:{light:{model:"gpt-6-luna",effort:"low"}}}}' <<<"$valid")"
  "no-entry:$(jq -c 'del(.roles.codex.implement.light)' <<<"$valid")"
  "no-role:$(jq -c 'del(.roles.codex.implement)' <<<"$valid")"
  "empty-model:$(jq -c '.roles.codex.implement.light.model=""' <<<"$valid")"
  "bad-model:$(jq -c '.roles.codex.implement.light.model="-x"' <<<"$valid")"
  "no-effort:$(jq -c 'del(.roles.codex.implement.light.effort)' <<<"$valid")"
  "empty-effort:$(jq -c '.roles.codex.implement.light.effort=""' <<<"$valid")"
  "unlisted-effort:$(jq -c '.roles.codex.implement.light.effort="ultra"' <<<"$valid")"
  "no-effort-list:$(jq -c 'del(.efforts.codex)' <<<"$valid")"
  "string-effort-list:$(jq -c '.efforts.codex="low medium" | .roles.codex.implement.light.effort="ow"' <<<"$valid")"
  "numeric-effort:$(jq -c '.efforts.codex=[1] | .roles.codex.implement.light.effort=1' <<<"$valid")"
  "array-effort:$(jq -c '.efforts.codex=[["low"]] | .roles.codex.implement.light.effort=["low"]' <<<"$valid")"
  "newline-model:$(jq -c '.roles.codex.implement.light.model="gpt-6-luna\n"' <<<"$valid")"
  "tab-model:$(jq -c '.roles.codex.implement.light.model="gpt\tlow"' <<<"$valid")"
  "subst-model:$(jq -c '.roles.codex.implement.light.model="$(id)"' <<<"$valid")"
  "listed-bad-effort:$(jq -c '.efforts.codex=["--x"] | .roles.codex.implement.light.effort="--x"' <<<"$valid")"
  "listed-newline-effort:$(jq -c '.efforts.codex=["low\n"] | .roles.codex.implement.light.effort="low\n"' <<<"$valid")"
  "not-json:{"
)
printf '%s\n' "$valid" >"$tmp/badcfg/.agents/config/models.json"
fixture_policy "$tmp/badcfg-ok"
(cd "$tmp/work" && XDG_STATE_HOME="$tmp/badcfg-ok" \
  bash "$tmp/badcfg/.agents/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >/dev/null 2>&1) \
  || { echo 'FAIL: valid copied policy refused' >&2; exit 1; }
for item in "${bad_cases[@]}"; do
  label=${item%%:*}
  printf '%s\n' "${item#*:}" >"$tmp/badcfg/.agents/config/models.json"
  fixture_policy "$tmp/badcfg-$label"
  if (cd "$tmp/work" && XDG_STATE_HOME="$tmp/badcfg-$label" TEST_ACTIONS="$tmp/badcfg-$label-actions" \
    bash "$tmp/badcfg/.agents/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >/dev/null 2>"$tmp/badcfg-$label-error"); then
    echo "FAIL: invalid model policy accepted: $label" >&2; exit 1
  fi
  grep -q 'no valid model for codex implement light' "$tmp/badcfg-$label-error" \
    || { echo "FAIL: unexpected refusal for $label" >&2; exit 1; }
  if grep -Eq '^(pane split|agent start)$' "$tmp/badcfg-$label-actions"; then
    echo "FAIL: child launched for $label" >&2; exit 1
  fi
  jq -e '.reservations | length == 0' "$tmp/badcfg-$label/ai-agent-workflow-lite/launch-gate/$LAUNCH_TASK_ID/state.json" >/dev/null \
    || { echo "FAIL: reservation made for $label" >&2; exit 1; }
  if compgen -G "$tmp/badcfg-$label/ai-agent-workflow-lite/*/run.*" >/dev/null; then
    echo "FAIL: run directory created for $label" >&2; exit 1
  fi
done
# Every shipped role/tier resolves through the same selector run-agent.sh uses.
for p in codex claude; do for r in implement review; do for t in light standard deep; do
  jq -er --arg p "$p" --arg r "$r" --arg t "$t" -f "$base/.agents/scripts/select-model.jq" \
    "$base/.agents/config/models.json" >/dev/null || { echo "FAIL: shipped policy $p $r $t" >&2; exit 1; }
done; done; done
# Issue #43: token usage comes from the child's own session record when Herdr has none.
summary_tokens=$(jq -c '.token_usage' "$summary")
[[ $summary_tokens == null ]] || { echo "FAIL: token usage without a session record: $summary_tokens" >&2; exit 1; }
fixture_policy "$tmp/tokens"
(cd "$tmp/work" && XDG_STATE_HOME="$tmp/tokens" TEST_CODEX_SESSION=1 TEST_NO_HERDR_TOKENS=1 TEST_ACTIONS="$tmp/tokens-actions" \
  bash "$base/.agents/scripts/run-agent.sh" implement codex light "$tmp/task.txt" >"$tmp/tokens-output" 2>&1)
tsum=$(find "$tmp/tokens" -name summary.json -type f | head -n1)
# Issue #71: the final reply comes from the record into a 0600 result.md; the screen is not read.
check_result() {
  local sum=$1 out=$2 actions=$3 body=$4 path
  path=$(jq -r '.result_path' "$sum")
  [[ $path == "$(dirname "$sum")/result.md" && $(stat -c %a "$path") == 600 && $(cat "$path") == "$body" ]] \
    || { echo "FAIL: result.md not written: $path" >&2; exit 1; }
  jq -e --argjson n "$(wc -c <"$path")" '.result_bytes == $n and .phase == "result_collected" and .status == "returned"' "$sum" >/dev/null
  grep -Fqx "Result: path=$path bytes=$(wc -c <"$path")" "$out" || { echo 'FAIL: result location not shown' >&2; exit 1; }
  if grep -q '^agent read' "$actions" || grep -q '^result: done' "$out"; then
    echo 'FAIL: screen read used although result.md exists' >&2; exit 1
  fi
  [[ ! -e "$(dirname "$sum")/.result.tmp" ]]
}
check_result "$tsum" "$tmp/tokens-output" "$tmp/tokens-actions" $'result: done\nnext: from-record'
jq -e '.token_usage == {source:"codex-session",input_tokens:500,cache_read_tokens:400,cache_write_tokens:0,output_tokens:50,reasoning_tokens:7,total_tokens:950,cost_usd:null}
  and .input_tokens == null and .output_tokens == null and (.started_epoch | type == "number")' "$tsum" >/dev/null \
  || { echo "FAIL: session token usage not recorded: $(jq -c '{input_tokens,output_tokens,token_usage}' "$tsum")" >&2; exit 1; }
# Claude writes cost-state only at exit, so collection time records null and finish-agent.sh retries.
claude_policy "$tmp/tokens-claude"
mkdir -p "$CLAUDE_CONFIG_DIR/projects/p"
printf '%s\n' "{\"type\":\"user\",\"cwd\":\"$tmp/work\"}" >"$CLAUDE_CONFIG_DIR/projects/p/live.jsonl"
(cd "$tmp/work" && XDG_STATE_HOME="$tmp/tokens-claude" \
  bash "$base/.agents/scripts/run-agent.sh" review claude standard "$tmp/task.txt" >/dev/null 2>&1)
csum=$(find "$tmp/tokens-claude" -name summary.json -type f | head -n1)
jq -e '.status == "returned" and .token_usage == null' "$csum" >/dev/null \
  || { echo 'FAIL: claude usage recorded before exit' >&2; exit 1; }
claude_policy "$tmp/result-claude"
(cd "$tmp/work" && XDG_STATE_HOME="$tmp/result-claude" TEST_CLAUDE_SESSION=1 TEST_ACTIONS="$tmp/result-claude-actions" \
  bash "$base/.agents/scripts/run-agent.sh" review claude standard "$tmp/task.txt" >"$tmp/result-claude-output" 2>&1)
rsum=$(find "$tmp/result-claude" -name summary.json -type f | head -n1)
check_result "$rsum" "$tmp/result-claude-output" "$tmp/result-claude-actions" $'result: done\nnext: claude-record'
jq -e '.token_usage == null' "$rsum" >/dev/null
python3 "$base/tests/test-session-tokens.py" >/dev/null 2>&1 || { echo 'FAIL: session-tokens unit tests' >&2; exit 1; }
printf 'PASS: context, delegation, bounded output, external state, refusal, role/tier policy, token usage, result.md\n'
