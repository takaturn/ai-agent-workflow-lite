#!/bin/sh
# Registered for the Agent tool in .claude/settings.json (Issue #32).
# Prints the hook's output only when it is "ask", "deny" or context-only; any
# other outcome (timeout, non-zero exit, empty/invalid/allow output, missing
# timeout or python3) prints a fallback and exits 0: "deny" when the event is
# from a dontAsk session (nobody can answer a prompt there), otherwise "ask".
# Inner limits (6s + 2s) stay below the settings timeout (10s).
dir=$(dirname -- "$0")
input=$(cat)
case $input in
  *'"permission_mode":"dontAsk"'*|*'"permission_mode": "dontAsk"'*) decision=deny ;;
  *) decision=ask ;;
esac
fallback='{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"'$decision'","permissionDecisionReason":"Agent hookの判断を確認できませんでした。委譲手段の条件は .agents/skills/orchestrate/SKILL.md を参照。"}}'
if out=$(printf '%s' "$input" | timeout --kill-after=1s 5s python3 -B "$dir/agent-delegation-hook.py" 2>/dev/null) &&
   printf '%s' "$out" | timeout --kill-after=1s 1s python3 -B "$dir/agent-delegation-hook.py" validate 2>/dev/null; then
  printf '%s\n' "$out"
else
  printf '%s\n' "$fallback"
fi
exit 0
