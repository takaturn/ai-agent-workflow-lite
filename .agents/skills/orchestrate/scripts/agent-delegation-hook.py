#!/usr/bin/env python3
"""Claude Code PreToolUse hook for the Agent tool (Issue #32).

decide: read the hook event on stdin and print one JSON object.
  Explore / claude-code-guide without isolation -> no decision, context only.
  Anything else, unparsable input or an internal error -> "ask".
  permission_mode "dontAsk" -> "deny" for every type. This is a session-mode
  check, not proof of a child: an orchestrate Writer runs in dontAsk, nobody can
  answer a prompt there, and children must not delegate. A Root run in dontAsk
  also loses Agent; run it in another mode to delegate.
  Never "allow": the existing permission settings still apply.
validate: read decide's output on stdin; exit 0 only for ask, deny or context-only.
"""
import json
import sys

sys.dont_write_bytecode = True
PASS_TYPES = frozenset({"Explore", "claude-code-guide"})
CONDITIONS = (
    "委譲手段の条件（.agents/skills/orchestrate/SKILL.md）: 内蔵subagentは、読み取りのみ・文脈の引き継ぎが必要・"
    "数分で終わりRootがその場で確かめられる・独立レビュー不要のすべてを満たすときだけ。ファイル変更の並行作業、"
    "独立レビュー、Root以外のモデル、長時間・切り離し、summary.jsonや終了ゲートでの追跡はorchestrate。"
    "条件は起動の権限を与えず、枠・承認・Herdrが使えないときに黙ってsubagentへ切り替えない。"
    "判断できないときだけ、比較とおすすめと根拠をユーザーに示す。"
)
CHILD_DENY = ("dontAskのセッション（orchestrateのWriterなど）では確認に答える人がおらず、子の再帰的な委譲も禁止されているため、Agentを拒否した。"
              "委譲が必要ならRootに報告する（.agents/skills/orchestrate/SKILL.md）。")
CONTEXT = "Agent hook: 読み取り専用の種類として判断を出さず、既存の権限設定に従う。独立レビューには数えない。"


def ask(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "ask",
                                   "permissionDecisionReason": reason + CONDITIONS}}


def deny():
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny",
                                   "permissionDecisionReason": CHILD_DENY}}


def decide(raw):
    data = json.loads(raw)
    if not isinstance(data, dict):
        return ask("Agent hookの入力を判断できません。")
    if data.get("permission_mode") == "dontAsk":
        return deny()
    if data.get("tool_name") != "Agent":
        return ask("Agent hookの入力を判断できません。")
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return ask("Agent hookの入力を判断できません。")
    if "isolation" in tool_input:
        return ask("isolationを指定した委譲です。")
    kind = tool_input.get("subagent_type")
    if isinstance(kind, str) and kind in PASS_TYPES:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": CONTEXT}}
    return ask("読み取り専用と判断できない種類の委譲です。")


def valid(text):
    data = json.loads(text)
    out = data["hookSpecificOutput"] if set(data) == {"hookSpecificOutput"} else None
    if not isinstance(out, dict) or out.get("hookEventName") != "PreToolUse":
        return False
    if set(out) == {"hookEventName", "permissionDecision", "permissionDecisionReason"}:
        return (out["permissionDecision"] in ("ask", "deny")
                and isinstance(out["permissionDecisionReason"], str))
    return set(out) == {"hookEventName", "additionalContext"} and isinstance(out["additionalContext"], str)


def main():
    if sys.argv[1:] == ["validate"]:
        try:
            return 0 if valid(sys.stdin.read()) else 1
        except BaseException:
            return 1
    try:
        result = decide(sys.stdin.read())
    except BaseException:
        result = ask("Agent hookで異常が発生しました。")
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
