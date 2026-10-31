#!/usr/bin/env python3
"""Offline Root exit check. No model, network, pane or publication operations."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time

LIMIT = 262144


def require(condition):
    if not condition:
        raise ValueError("invalid or mismatched exit-check input")


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def load(path):
    with open(path, "rb") as stream:
        raw = stream.read(LIMIT + 1)
    require(len(raw) <= LIMIT)
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: require(False))


def identity(value):
    require(isinstance(value, str) and 0 < len(value) <= 128)
    require(all(c.isascii() and (c.isalnum() or c in "_.:-") for c in value))
    return value


def exact(value, keys):
    require(isinstance(value, dict) and set(value) == set(keys.split()))


def canonical(value):
    require(isinstance(value, str) and Path(value).is_absolute())
    path = Path(value).resolve(strict=True)
    require(str(path) == value)
    return path


def task_header(task, session, parent, cwd, task_id):
    exact(task, "version task_id session_id parent_pane_id worktree stop work runs worktrees")
    require(type(task["version"]) is int and task["version"] == 1)
    require(identity(task["task_id"]) == identity(task_id))
    require(identity(task["session_id"]) == identity(session))
    require(identity(task["parent_pane_id"]) == identity(parent))
    require(canonical(task["worktree"]) == canonical(cwd))
    require(task["stop"] in (None, "explicit_stop", "task_budget"))


def run_home(state_parent, worktree):
    repo_id = hashlib.sha256(str(worktree).encode()).hexdigest()[:16]
    return state_parent / repo_id


def inspect(task, session, parent, cwd, task_id, ledger, summary_home, state_home=None):
    """All identities are explicitly supplied attestations, not authentication."""
    task_header(task, session, parent, cwd, task_id)
    # Task stop is authoritative even if later work/summary data is incomplete.
    if task["stop"]:
        return {"outcome": "stopped", "reason": task["stop"], "next": []}
    require(isinstance(ledger, dict))
    spec = importlib.util.spec_from_file_location(
        "launch_gate", Path(__file__).with_name("launch-gate.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.validate(ledger, task_id)
    if ledger["stop"]["reason"] == "explicit_stop":
        return {"outcome": "stopped", "reason": "explicit_stop", "next": []}
    require(isinstance(task["work"], list) and len(task["work"]) <= 1000)
    require(isinstance(task["runs"], list) and len(task["runs"]) <= 1000)
    require(isinstance(task["worktrees"], list) and 1 <= len(task["worktrees"]) <= 100)
    next_steps, blocked, seen = [], [], set()
    for item in task["work"]:
        exact(item, "id kind state")
        key = identity(item["id"])
        require(key not in seen)
        seen.add(key)
        require(item["kind"] in ("root", "child_launch", "publish", "read_back"))
        require(item["state"] in ("done", "ready", "permission", "external"))
        if item["state"] == "done":
            continue
        step = {"work_id": key, "action": item["kind"]}
        if item["state"] in ("permission", "external"):
            blocked.append({**step, "reason": item["state"]})
        elif item["kind"] == "child_launch" and ledger["stop"]["reason"] == "limit_reached":
            blocked.append({**step, "reason": "launch_limit"})
        else:
            next_steps.append(step)
    home = canonical(summary_home)
    require(home != canonical(cwd) and canonical(cwd) not in home.parents)
    if state_home is not None:
        state_root = canonical(state_home)
        require(home.parent == state_root / "ai-agent-workflow-lite")
    worktrees = [canonical(value) for value in task["worktrees"]]
    require(len(worktrees) == len(set(worktrees)) and canonical(cwd) in worktrees)
    require(home == run_home(home.parent, canonical(cwd)))
    homes = {str(worktree): run_home(home.parent, worktree) for worktree in worktrees}
    discovered = set()
    for worktree, run_dir in homes.items():
        require(run_dir.is_dir() and canonical(str(run_dir)) == run_dir)
        for path in run_dir.glob("run.*/summary.json"):
            require(canonical(str(path)) == path)
            summary = load(path)
            require(isinstance(summary, dict))
            if summary.get("task_id") != task_id:
                continue
            require(summary.get("parent_pane_id") == parent)
            require(summary.get("worktree") == worktree)
            discovered.add(str(path))
    reservations = {r["id"]: r for r in ledger["reservations"]}
    listed, run_ids = set(), set()
    for run in task["runs"]:
        exact(run, "run_id reservation_id worktree summary")
        run_id = identity(run["run_id"])
        require(run_id.startswith("run.") and run_id not in run_ids)
        run_ids.add(run_id)
        reservation_id = identity(run["reservation_id"])
        require(reservation_id not in listed)
        listed.add(reservation_id)
        reservation = reservations.get(reservation_id)
        require(reservation is not None and reservation["run_id"] == run_id)
        worktree = str(canonical(run["worktree"]))
        require(worktree in homes)
        require(reservation["worktree"] == worktree)
        path = canonical(run["summary"])
        require(homes[worktree] == path.parent.parent and path.name == "summary.json")
        require(path.parent.name == run_id)
        summary = load(path)
        require(isinstance(summary, dict))
        for key, value in (("task_id", task_id), ("parent_pane_id", parent),
                           ("run_id", run_id), ("worktree", worktree),
                           ("reservation_id", reservation_id)):
            require(summary.get(key) == value)
        status = summary.get("status")
        require(status in ("running", "returned", "failed", "interrupted", "prelaunch_error"))
        require(summary.get("phase") in ("initialized", "pane_created", "agent_ready",
                                         "prompt_wait", "result_collected"))
        require(summary.get("root_acceptance") in ("pending", "accepted"))
        require(summary.get("cleanup") in ("retained", "closed"))
        if status == "running":
            require(reservation["status"] == "reserved")
            next_steps.append({"run_id": run_id, "action": "collect_result"})
        elif status != "returned":
            require(reservation["status"] == status)
            blocked.append({"run_id": run_id, "reason": "diagnose_failed_run"})
        else:
            require(reservation["status"] == "returned" and type(summary.get("exit_code")) is int
                    and summary["exit_code"] == 0)
            require(identity(summary.get("pane_id")) != parent)
            identity(summary.get("agent_name"))
            if summary["phase"] != "result_collected":
                next_steps.append({"run_id": run_id, "action": "collect_result"})
            elif summary["root_acceptance"] == "pending":
                next_steps.append({"run_id": run_id, "action": "verify_and_accept"})
            else:
                require(summary.get("accepted_by") == parent)
                require(isinstance(summary.get("acceptance_note"), str)
                        and bool(summary["acceptance_note"].strip()))
                if summary["cleanup"] != "closed":
                    next_steps.append({"run_id": run_id, "action": "verify_cleanup"})
    # The task-wide ledger is an independent inventory; omission cannot pass done.
    require(listed == set(reservations))
    require(discovered == {run["summary"] for run in task["runs"]})
    if next_steps:
        return {"outcome": "incomplete", "reason": "executable_work",
                "next": next_steps, "blocked": blocked}
    if blocked:
        return {"outcome": "stopped", "reason": "blocked_work", "next": [], "blocked": blocked}
    return {"outcome": "complete", "reason": "all_registered_checks_complete", "next": []}


def hook_decision(result, event, state, session, parent, cwd, task_id, now):
    """Pure transition; the lock-holding adapter persists before emitting JSON."""
    require(isinstance(event, dict) and event.get("hook_event_name") == "Stop")
    require(event.get("session_id") == session and event.get("cwd") == cwd)
    require(type(event.get("stop_hook_active")) is bool)
    identity(event.get("turn_id"))
    exact(state, "version task_id session_id parent_pane_id worktree started_at deadline max_continuations continuations stopped")
    require(type(state["version"]) is int and state["version"] == 1)
    for key, expected in (("task_id", task_id), ("session_id", session),
                          ("parent_pane_id", parent), ("worktree", cwd)):
        require(state[key] == expected)
    for key in ("started_at", "deadline", "max_continuations", "continuations"):
        require(type(state[key]) is int)
    require(0 <= state["started_at"] <= now)
    require(0 < state["deadline"] - state["started_at"] <= 3600)
    require(0 <= state["continuations"] <= state["max_continuations"] <= 10)
    require(type(state["stopped"]) is bool)
    reason = result["reason"]
    if result["reason"] in ("explicit_stop", "task_budget"):
        pass
    elif state["stopped"]:
        reason = "previous_stop"
    elif now >= state["deadline"]:
        reason = "continuation_time_limit"
    elif state["continuations"] >= state["max_continuations"]:
        reason = "continuation_count_limit"
    elif result["outcome"] == "incomplete":
        state["continuations"] += 1
        # Do not repeat user text, paths, summary notes, or arbitrary hook input.
        reason = "Root exit check: executable registered work remains. " + json.dumps(result["next"])
        return {"decision": "block", "reason": reason}, state
    state["stopped"] = True
    return {"continue": False, "stopReason": reason}, state


def adapt(result, args):
    require(not Path(args.continuation_state).is_symlink())
    state_path = canonical(args.continuation_state)
    cwd = canonical(args.cwd)
    require(cwd not in state_path.parents)
    parent_stat = state_path.parent.stat()
    require(parent_stat.st_uid == os.getuid() and parent_stat.st_mode & 0o077 == 0)
    # Existing private, non-symlink state and lock must be provisioned by Root.
    for path in (state_path, state_path.with_suffix(".lock")):
        require(not path.is_symlink() and path.is_file())
        stat = path.stat()
        require(stat.st_uid == os.getuid() and stat.st_mode & 0o077 == 0)
    raw = sys.stdin.buffer.read(LIMIT + 1)
    require(len(raw) <= LIMIT)
    event = json.loads(raw, object_pairs_hook=unique,
                       parse_constant=lambda _: require(False))
    fd = os.open(state_path.with_suffix(".lock"), os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(fd, "r+") as lock:
        locked = os.fstat(lock.fileno())
        require(locked.st_uid == os.getuid() and locked.st_mode & 0o077 == 0)
        # A competing hook invocation stops; never wait unboundedly at shutdown.
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_fd = os.open(state_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(state_fd, "rb") as state_stream:
            opened = os.fstat(state_stream.fileno())
            require(opened.st_uid == os.getuid() and opened.st_mode & 0o077 == 0)
            raw_state = state_stream.read(LIMIT + 1)
            require(len(raw_state) <= LIMIT)
            state = json.loads(raw_state, object_pairs_hook=unique,
                               parse_constant=lambda _: require(False))
        output, state = hook_decision(result, event, state,
                                     args.session_id, args.parent_pane_id, args.cwd,
                                     args.task_id, int(time.time()))
        descriptor, temporary = tempfile.mkstemp(prefix=".exit-", dir=state_path.parent)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(state, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, state_path)
            directory_fd = os.open(state_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return output


def terminalize_error(args):
    """Persist a terminal decision when the bound state is valid."""
    if not args.continuation_state:
        return
    event = {"hook_event_name": "Stop", "session_id": args.session_id,
             "cwd": args.cwd, "turn_id": "error", "stop_hook_active": True}
    result = {"outcome": "stopped", "reason": "invalid_or_unknown_input", "next": []}
    original_stdin = sys.stdin
    try:
        # adapt consumes a Stop event; this internal event carries no user text.
        import io
        sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(event).encode()))
        adapt(result, args)
    finally:
        sys.stdin = original_stdin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("task", "task-id", "session-id", "parent-pane-id", "cwd", "summary-home"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--hook", action="store_true", help="opt-in Codex Stop JSON adapter")
    parser.add_argument("--continuation-state", help="existing external private state; required for --hook")
    args = parser.parse_args()
    try:
        cwd = canonical(args.cwd)
        task_path = canonical(args.task)
        require(cwd not in task_path.parents)
        task = load(task_path)
        task_header(task, args.session_id, args.parent_pane_id, args.cwd, args.task_id)
        if task["stop"]:
            result = {"outcome": "stopped", "reason": task["stop"], "next": []}
        else:
            state_home = canonical(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
            ledger_path = state_home / "ai-agent-workflow-lite/launch-gate" / identity(args.task_id) / "state.json"
            result = inspect(task, args.session_id, args.parent_pane_id,
                             args.cwd, args.task_id, load(ledger_path), args.summary_home,
                             str(state_home))
        if args.hook:
            require(args.continuation_state is not None)
            output = adapt(result, args)
    except (ValueError, OSError, TypeError, KeyError, RecursionError, OverflowError):
        reason = result.get("reason") if "result" in locals() else None
        if reason not in ("explicit_stop", "task_budget"):
            reason = "invalid_or_unknown_input"
        result = {"outcome": "stopped", "reason": reason, "next": []}
        output = {"continue": False, "stopReason": reason}
        if args.hook:
            try:
                terminalize_error(args)
            except (ValueError, OSError, TypeError, KeyError, RecursionError, OverflowError):
                pass
    if args.hook:
        print(json.dumps(output, ensure_ascii=True, sort_keys=True))
        return 0
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result["outcome"] == "complete" else 2


if __name__ == "__main__":
    sys.exit(main())
