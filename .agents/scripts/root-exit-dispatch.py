#!/usr/bin/env python3
"""User-level Codex SessionStart/Stop hook: find the Root automatically and run the exit check."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

spec = importlib.util.spec_from_file_location("root_exit", Path(__file__).with_name("root-exit-check.py"))
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
MARKER = ".agents/scripts/root-exit-check.py"
KINDS = "root|child_launch|publish|read_back"
STATES = "done|ready|permission|external"


def emit(value):
    print(json.dumps(value, ensure_ascii=True, sort_keys=True))
    return 0


def target(event):
    """Return (session, pane, root) for a Root in this workflow, else None (hook is a no-op)."""
    if os.environ.get("HERDR_ENV") != "1" or not os.environ.get("HERDR_PANE_ID"):
        return None
    cwd = event.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_dir():
        return None
    top = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, timeout=3)
    if top.returncode != 0 or not (Path(top.stdout.strip()) / MARKER).is_file():
        return None
    pane = checker.identity(os.environ["HERDR_PANE_ID"])
    root = checker.state_root()
    if checker.is_child(root, pane):
        return None
    return checker.identity(event.get("session_id")), pane, root


def guide(path, session):
    return (f"Root exit gate: this session ({session}) is checked before it ends. Before implementing or "
            f"delegating, write {path} as JSON: "
            '{"version":1,"task_id":<LAUNCH_TASK_ID or null>,"stop":null,"out_of_scope":null,'
            f'"work":[{{"id":"...","kind":"{KINDS}","state":"{STATES}"}}]}}. '
            "List every required item (implementation, tests, Wiki/Issue/PR updates and their read-back); "
            "mark done only after verifying it. Child runs from this pane are collected automatically. "
            'If this session has no task to track, set "out_of_scope" to a short reason and "work" to []. '
            'Set "stop" only for a real user stop ("explicit_stop") or budget stop ("task_budget").')


def mark_began(directory):
    """Create once; touching an existing marker would move the session start forward."""
    began = directory / "session-began"
    if not began.exists():
        began.touch()
    return began


def require_bool(value):
    checker.require(type(value) is bool)


def write_state(path, state):
    fd, temporary = tempfile.mkstemp(prefix=".state.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream, sort_keys=True)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def stop(event, session, pane, root):
    directory = root / "root-exit" / session
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    work = directory / "work.json"
    state_path = directory / "continuation.json"
    began = mark_began(directory)
    with open(directory / ".lock", "a+") as lock:
        # A competing Stop never waits at shutdown and never reports completion.
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = int(time.time())
        require_bool(event.get("stop_hook_active"))
        try:
            state = checker.load(state_path) if state_path.exists() else checker.new_state(now)
            checker.require(isinstance(state, dict))
        except ValueError:
            if event["stop_hook_active"]:
                raise
            state = {}
        if not event["stop_hook_active"]:
            # A user-started turn gets a fresh, bounded continuation budget; hook-driven
            # continuations of the same turn keep counting against it.
            state = {**checker.new_state(now), "asked": state.get("asked") is True}
        if not work.exists():
            if state["asked"] or state["stopped"]:
                output = {"continue": False, "stopReason": "work_not_declared"}
                state["stopped"] = True
            else:
                state["asked"] = True
                output = {"decision": "block", "reason": guide(work, session)}
        else:
            try:
                decl = checker.declaration(checker.load(work))
            except ValueError:
                if state["asked"]:
                    raise
                # Give one chance to repair a malformed declaration instead of ending silently.
                state["asked"] = True
                write_state(state_path, state)
                return {"decision": "block", "reason": "work.json is invalid. " + guide(work, session)}
            runs = checker.owned_runs(root, pane, decl["task_id"], began.stat().st_mtime)
            result = checker.inspect(decl, runs, checker.ledger_for(root, decl["task_id"]))
            output, state = checker.hook_decision(result, state, now)
        write_state(state_path, state)
    return output


def main():
    mode = sys.argv[1] if len(sys.argv) == 2 else None
    try:
        event = json.loads(sys.stdin.read(checker.LIMIT + 1) or "{}")
        found = target(event) if isinstance(event, dict) else None
    except (ValueError, OSError, subprocess.SubprocessError):
        # Outside a recognisable Root, never interfere with unrelated sessions.
        return emit({})
    if found is None:
        return emit({})
    session, pane, root = found
    if mode == "session-start":
        path = root / "root-exit" / session / "work.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            mark_began(path.parent)
        except OSError:
            pass
        return emit({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                            "additionalContext": guide(path, session)}})
    if mode != "stop" or event.get("hook_event_name") != "Stop":
        return emit({})
    try:
        return emit(stop(event, session, pane, root))
    except (ValueError, OSError, TypeError, KeyError, RecursionError):
        # Fail closed: an unreadable state never turns into an automatic continuation.
        return emit({"continue": False, "stopReason": "root_exit_check_error"})


if __name__ == "__main__":
    sys.exit(main())
