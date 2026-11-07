#!/usr/bin/env python3
"""Model-free fixtures for the Agent delegation hook (Issue #32).

Runs the exact command registered in .claude/settings.json, and runs the
wrapper against fake hook scripts to cover failure outcomes.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agents/skills/orchestrate/scripts"


def registered():
    settings = json.loads((ROOT / ".claude/settings.json").read_text())
    found = [h for entry in settings["hooks"]["PreToolUse"]
             if entry.get("matcher") == "Agent" for h in entry["hooks"]]
    assert len(found) == 1, found
    return found[0]


def run(command, stdin, project_dir):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project_dir))
    proc = subprocess.run(["sh", "-c", command], input=stdin, text=True,
                          capture_output=True, env=env, timeout=15)
    assert proc.returncode == 0, proc
    out = json.loads(proc.stdout)
    assert set(out) == {"hookSpecificOutput"}, out
    inner = out["hookSpecificOutput"]
    assert inner["hookEventName"] == "PreToolUse", inner
    assert "allow" != inner.get("permissionDecision"), inner
    return inner


def payload(mode="auto", **tool_input):
    return json.dumps({"session_id": "s", "cwd": str(ROOT), "hook_event_name": "PreToolUse",
                       "permission_mode": mode, "tool_name": "Agent", "tool_input": tool_input})


def main():
    hook = registered()
    command = hook["command"]
    # Inner limits: decide 5s + 1s kill, validate 1s + 1s kill, below the settings timeout.
    wrapper = (SCRIPTS / "agent-delegation-hook.sh").read_text()
    limits = [int(a) + int(b) for a, b in re.findall(r"timeout --kill-after=(\d+)s (\d+)s", wrapper)]
    assert len(limits) == 2 and sum(limits) < hook["timeout"], (limits, hook["timeout"])

    cases = [
        (payload(subagent_type="Explore", prompt="x"), None),
        (payload(subagent_type="claude-code-guide", prompt="x"), None),
        (payload(subagent_type="Explore", isolation="worktree", prompt="x"), "ask"),
        (payload(subagent_type="general-purpose", prompt="x"), "ask"),
        (payload(subagent_type="fork", prompt="x"), "ask"),
        (payload(subagent_type="Plan", prompt="x"), "ask"),
        (payload(prompt="x"), "ask"),
        (payload(subagent_type=["Explore"], prompt="x"), "ask"),
        (json.dumps({"tool_name": "Bash", "tool_input": {}}), "ask"),
        (json.dumps({"tool_name": "Agent", "tool_input": "Explore"}), "ask"),
        (payload(mode="dontAsk", subagent_type="Explore", prompt="x"), "deny"),
        (payload(mode="dontAsk", subagent_type="general-purpose", prompt="x"), "deny"),
        (json.dumps({"tool_name": "Agent", "tool_input": {"subagent_type": "Explore"}}), None),
        (payload(subagent_type="Explore", isolation=None, prompt="x"), "ask"),
        (payload(subagent_type="explore", prompt="x"), "ask"),
        ("not json", "ask"),
        ("", "ask"),
    ]
    for stdin, expected in cases:
        out = run(command, stdin, ROOT)
        assert out.get("permissionDecision") == expected, (stdin, out)
        if expected is None:
            assert set(out) == {"hookEventName", "additionalContext"}, out
            assert "独立レビューには数えない" in out["additionalContext"], out
        elif expected == "deny":
            assert "Rootに報告" in out["permissionDecisionReason"], out
        else:
            assert "orchestrate" in out["permissionDecisionReason"], out
            assert "独立レビュー" in out["permissionDecisionReason"], out

    # Failure outcomes of the hook script: the wrapper must still print ask and exit 0.
    fakes = {
        "allow": 'print(\'{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"}}\')',
        "allow_ctx": 'print(\'{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"x"}}\')',
        "empty": "pass",
        "partial": 'print(\'{"hookSpecificOutput":{"hookEventName"\')',
        "exit2": 'import sys; print(\'{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"x"}}\'); sys.exit(2)',
        "stderr": 'import sys; sys.stderr.write("boom\\n")',
        "hang": "import time; time.sleep(30)",
        "two_objects": 'print(\'{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"x"}}\\n{}\')',
        "extra_key": 'print(\'{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"x","permissionDecision":"allow"}}\')',
    }
    with tempfile.TemporaryDirectory() as tmp:
        scripts = Path(tmp) / ".agents/skills/orchestrate/scripts"
        scripts.mkdir(parents=True)
        shutil.copy(SCRIPTS / "agent-delegation-hook.sh", scripts)
        for name, body in fakes.items():
            # validate mode keeps the real validator so only decide misbehaves.
            real = (SCRIPTS / "agent-delegation-hook.py").read_text()
            (scripts / "agent-delegation-hook.py").write_text(
                "import sys\nif sys.argv[1:] != ['validate']:\n"
                + "".join("    " + line + "\n" for line in body.split("\n"))
                + "    sys.exit(0)\n" + real)
            # Both modes run at once so the hang waits for the wrapper timeout only once.
            # In a dontAsk session the same failure must deny, not leave a prompt waiting.
            with ThreadPoolExecutor(max_workers=2) as pool:
                asked = pool.submit(run, command, payload(subagent_type="Explore"), tmp)
                denied = pool.submit(run, command, payload(mode="dontAsk", subagent_type="Explore"), tmp)
                asked, denied = asked.result(), denied.result()
            assert asked.get("permissionDecision") == "ask", (name, asked)
            assert denied.get("permissionDecision") == "deny", (name, denied)
        # Wrapper missing: the registered command's own fallback asks.
        shutil.rmtree(Path(tmp) / ".agents")
        assert run(command, payload(subagent_type="Explore"), tmp)["permissionDecision"] == "ask"
        assert run(command, payload(mode="dontAsk", subagent_type="Explore"), tmp)["permissionDecision"] == "deny"
    print(f"agent delegation hook: {len(cases) + 2 * len(fakes) + 3} cases passed")


if __name__ == "__main__":
    main()
