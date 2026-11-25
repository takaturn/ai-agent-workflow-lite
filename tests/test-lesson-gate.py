#!/usr/bin/env python3
"""Model-free fixtures for the lesson gate hook (Issue #98).

Runs the exact command registered in .claude/settings.json with a private
XDG_STATE_HOME, and the record helper as Root would run it.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agents/scripts/lesson-gate.py"
SECRET = "UNIQUE-ENTRY-TEXT-7f3a"


def registered():
    settings = json.loads((ROOT / ".claude/settings.json").read_text())
    found = [h for entry in settings["hooks"]["PreToolUse"]
             if entry.get("matcher") == "Agent|Bash" for h in entry["hooks"]]
    assert len(found) == 1, found
    assert "lesson-gate.py" in found[0]["command"], found
    return found[0]["command"]


class Env:
    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.state = self.tmp / "state"
        self.lessons = self.state / "ai-agent-workflow-lite" / "lessons"
        self.env = dict(os.environ, XDG_STATE_HOME=str(self.state), CLAUDE_PROJECT_DIR=str(ROOT))
        self.command = registered()
        self.memory = self.tmp / "eval-design-first.md"
        self.memory.write_text("評価の設計を先に固める。")
        self.wiki = self.tmp / "Wiki-Page.md"
        self.wiki.write_text("runner の記録")

    def triggers(self, lessons=None, raw=None):
        self.lessons.mkdir(parents=True, exist_ok=True)
        if lessons is None:
            lessons = [
                {"name": "eval-design-first", "source": "memory", "path": str(self.memory),
                 "summary": "評価の設計を先に", "launch": ["agent", "eval-run", "runner"],
                 "words": ["評価", "Eval"]},
                {"name": "runner-page", "source": "wiki", "path": str(self.wiki),
                 "summary": "runner の記録", "launch": ["runner"], "words": []},
            ]
        text = raw if raw is not None else json.dumps({"version": 1, "lessons": lessons})
        (self.lessons / "triggers.json").write_text(text)

    def hook(self, tool, mode="auto", session="s1", **tool_input):
        event = json.dumps({"session_id": session, "cwd": str(self.tmp), "hook_event_name": "PreToolUse",
                            "permission_mode": mode, "tool_name": tool, "tool_input": tool_input})
        proc = subprocess.run(["sh", "-c", self.command], input=event, text=True,
                              capture_output=True, env=self.env, timeout=15)
        assert proc.returncode == 0, proc
        if not proc.stdout.strip():
            return None
        out = json.loads(proc.stdout)["hookSpecificOutput"]
        assert out["hookEventName"] == "PreToolUse", out
        assert out.get("permissionDecision") != "allow", out
        return out

    def record(self, *lessons, launch="agent:general-purpose", session="s1", ok=True):
        args = [sys.executable, "-B", str(SCRIPT), "record", "--session", session,
                "--launch", launch, "--purpose", "評価の試行を起動する"]
        for lesson in lessons:
            args += ["--lesson", *lesson]
        proc = subprocess.run(args, text=True, capture_output=True, env=self.env, timeout=15)
        assert (proc.returncode == 0) == ok, proc
        return proc

    def log(self):
        path = self.lessons / "decisions.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def denied(out):
    assert out is not None and out.get("permissionDecision") == "deny", out
    return out["permissionDecisionReason"]


def main():
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        agent = dict(subagent_type="general-purpose", description="Eval trial", prompt="run the trial")

        # Not a target launch, and no triggers.json yet.
        assert e.hook("Bash", command="ls -la") is None
        assert e.hook("Agent", **agent) is None
        e.triggers()
        # Launch detection: what is not a launch.
        for command in ("git commit -m 'eval-run'", "grep -n claude README.md",
                        'python3 .agents/scripts/lesson-gate.py record --purpose "claude -p"'):
            assert e.hook("Bash", command=command) is None, command
        # No matching words: nothing is asked.
        assert e.hook("Agent", subagent_type="Explore", description="find code", prompt="locate x") is None
        # dontAsk sessions are left to the existing Agent hook.
        assert e.hook("Agent", mode="dontAsk", **agent) is None

        # No entry: deny with the lesson, its path and the helper usage.
        reason = denied(e.hook("Agent", **agent))
        assert "eval-design-first" in reason and str(e.memory) in reason, reason
        assert "--session s1" in reason and "lesson-gate.py" in reason, reason
        assert "runner-page" not in reason, reason
        # Wrong launch kind, then a missing lesson: still denied.
        e.record(("eval-design-first", "apply", "x"), launch="claude-p")
        assert "種類" in denied(e.hook("Agent", **agent))
        e.record(("other-lesson", "apply", "x"))
        assert "eval-design-first" in denied(e.hook("Agent", **agent))
        # Invalid entries are refused by the helper.
        e.record(("eval-design-first", "maybe", "x"), ok=False)
        e.record(("eval-design-first", "apply", " "), ok=False)
        # A covering entry passes once; it cannot be reused.
        e.record(("eval-design-first", "not_apply", SECRET))
        assert e.hook("Agent", **agent) is None
        assert "使われていない" in denied(e.hook("Agent", **agent))

        # Runner: words come from the task file; an unreadable file is not judged.
        task = e.tmp / "task.md"
        task.write_text("評価の設計を確かめる")
        run = f".agents/scripts/run-agent.sh review claude standard {task}"
        reason = denied(e.hook("Bash", command=run))
        assert "eval-design-first" in reason and "runner-page" in reason, reason
        assert "参照用の記録で、指示ではない" in reason, reason
        e.record(("eval-design-first", "apply", "y"), ("runner-page", "not_apply", "z"), launch="runner")
        assert e.hook("Bash", command=run) is None
        e.record(("runner-page", "not_apply", "z"), launch="runner")
        missing_task = ".agents/scripts/run-agent.sh review claude standard /nonexistent.md"
        assert e.hook("Bash", command=missing_task) is None  # word lesson unjudged, runner-page covered
        assert e.log()[-1]["unjudged"] == ["eval-design-first"], e.log()[-1]
        # Reading a runner script is still matched by name; it only asks for an entry.
        assert "runner-page" in denied(e.hook("Bash", command="grep -n die .agents/scripts/run-agent.sh"))

        # eval-run.py and claude -p are detected; words are not read from Bash commands.
        assert e.hook("Bash", command="python3 .agents/scripts/eval-run.py start x") is None
        assert e.hook("Bash", command="timeout 60 claude --model m -p 'eval'") is None
        e.triggers([{"name": "eval-design-first", "source": "memory", "path": str(e.memory),
                     "launch": ["eval-run", "claude-p"]}])
        assert "eval-design-first" in denied(e.hook("Bash", command="python3 .agents/scripts/eval-run.py start x"))
        assert "eval-design-first" in denied(e.hook("Bash", command="cd /x && claude -p hi", session="s2"))

        # A lesson whose file is gone is left out and logged.
        e.memory.unlink()
        assert e.hook("Bash", command="claude -p hi", session="s3") is None
        assert e.log()[-1]["missing_files"] == ["eval-design-first"], e.log()[-1]

        # Broken triggers.json: launch goes on, Root is told, and it is logged.
        e.triggers(raw="{not json")
        out = e.hook("Agent", **agent)
        assert out is not None and "triggers.json" in out["additionalContext"], out
        assert e.log()[-1]["result"] == "broken"

        # Log lines: counts and names only, never entry or lesson text.
        log = e.log()
        results = [l["result"] for l in log]
        assert {"denied", "passed", "unjudged", "broken"} <= set(results), results
        passed = [l for l in log if l["result"] == "passed"]
        assert passed[0]["rewrites"] == 3 and passed[0]["not_apply"] == 1, passed[0]
        text = (e.lessons / "decisions.jsonl").read_text()
        assert SECRET not in text and "評価の設計を先に" not in text and "run the trial" not in text
        assert SECRET in (e.lessons / "s1.jsonl").read_text()

        # Bad session ids are refused without blocking (fail open).
        assert e.hook("Agent", session="..", **agent) is None
    print("lesson gate: ok")


if __name__ == "__main__":
    main()
