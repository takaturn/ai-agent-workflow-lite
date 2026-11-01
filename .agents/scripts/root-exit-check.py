#!/usr/bin/env python3
"""Offline Root exit check. No model, network, pane or publication operations."""
import argparse
import importlib.util
import json
import os
import subprocess
from pathlib import Path
import sys

LIMIT = 262144
STOPS = ("explicit_stop", "task_budget")


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


def state_root():
    raw = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))
    require(Path(raw).is_absolute())
    root = Path(raw).resolve()
    ancestor = root
    while not ancestor.exists():
        ancestor = ancestor.parent
    # Runtime data must stay outside Git so declarations are never staged or published.
    probe = subprocess.run(["git", "-C", str(ancestor), "rev-parse", "--show-toplevel"],
                           capture_output=True, timeout=3)
    require(probe.returncode != 0)
    return root / "ai-agent-workflow-lite"


def declaration(value):
    """The only Root-written input: required work, or an explicit out-of-scope reason."""
    exact(value, "version task_id stop out_of_scope work")
    require(type(value["version"]) is int and value["version"] == 1)
    if value["task_id"] is not None:
        identity(value["task_id"])
    require(value["stop"] in (None,) + STOPS)
    require(value["out_of_scope"] is None or
            (isinstance(value["out_of_scope"], str) and value["out_of_scope"].strip()))
    require(isinstance(value["work"], list) and len(value["work"]) <= 1000)
    # An empty template is not a declaration: either list work or say why none is tracked.
    require(value["work"] or value["out_of_scope"] is not None)
    return value


def summaries(root):
    """Every readable runner summary; unreadable ones cannot be attributed and are skipped."""
    for path in sorted(root.glob("*/run.*/summary.json")):
        try:
            summary = load(path)
            changed = path.stat().st_mtime
        except (OSError, ValueError):
            continue
        if isinstance(summary, dict):
            yield summary, changed


def is_child(root, pane):
    """A pane created by run-agent for a child is never a Root."""
    return any(s.get("pane_id") == pane and s.get("cleanup") != "closed" for s, _ in summaries(root))


def owned_runs(root, pane, task_id=None, since=None):
    """Runs from this Root pane for this task; with no task, only runs updated since the
    session began, so an earlier session's leftovers in a reused pane are not adopted."""
    return [s for s, changed in summaries(root) if s.get("parent_pane_id") == pane
            and (s.get("task_id") == task_id if task_id is not None
                 else since is None or changed >= since)]


def closed_receipt(summary):
    if (summary.get("root_acceptance") != "accepted" or summary.get("cleanup") != "closed"
            or summary.get("accepted_by") != summary.get("parent_pane_id")
            or not isinstance(summary.get("acceptance_note"), str)
            or not summary["acceptance_note"].strip()):
        return False
    if summary.get("status") == "returned":
        return summary.get("phase") == "result_collected" and summary.get("exit_code") == 0
    return (summary.get("status") in ("failed", "interrupted", "prelaunch_error")
            and isinstance(summary.get("failure_resolution"), str)
            and bool(summary["failure_resolution"].strip()))


def ledger_for(root, task_id):
    if task_id is None:
        return None
    path = root / "launch-gate" / task_id / "state.json"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(
        "launch_gate", Path(__file__).with_name("launch-gate.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ledger = load(path)
    module.validate(ledger, task_id)
    return ledger


def inspect(decl, runs, ledger):
    declaration(decl)
    # Task stop is authoritative even if later work/summary data is incomplete.
    if decl["stop"]:
        return {"outcome": "stopped", "reason": decl["stop"], "next": []}
    limit = None
    if ledger is not None:
        if ledger["stop"]["reason"] == "explicit_stop":
            return {"outcome": "stopped", "reason": "explicit_stop", "next": []}
        limit = ledger["stop"]["reason"]
    next_steps, blocked, seen = [], [], set()
    for item in decl["work"]:
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
        elif item["kind"] == "child_launch" and limit == "limit_reached":
            blocked.append({**step, "reason": "launch_limit"})
        else:
            next_steps.append(step)
    for summary in runs:
        run_id = summary.get("run_id")
        if closed_receipt(summary):
            continue
        status = summary.get("status")
        if status == "running" or (status == "returned" and summary.get("phase") != "result_collected"):
            next_steps.append({"run_id": run_id, "action": "collect_result"})
        elif status != "returned":
            blocked.append({"run_id": run_id, "reason": "diagnose_failed_run"})
        elif summary.get("root_acceptance") != "accepted":
            next_steps.append({"run_id": run_id, "action": "verify_and_accept"})
        elif summary.get("cleanup") != "closed":
            next_steps.append({"run_id": run_id, "action": "verify_cleanup"})
        else:
            # Accepted and closed but without a valid receipt (e.g. empty note).
            next_steps.append({"run_id": run_id, "action": "record_acceptance_note"})
    if next_steps:
        return {"outcome": "incomplete", "reason": "executable_work",
                "next": next_steps, "blocked": blocked}
    if blocked:
        return {"outcome": "stopped", "reason": "blocked_work", "next": [], "blocked": blocked}
    if decl["out_of_scope"] is not None and not decl["work"]:
        return {"outcome": "complete", "reason": "declared_out_of_scope", "next": []}
    return {"outcome": "complete", "reason": "all_declared_checks_complete", "next": []}


def new_state(now):
    return {"version": 1, "started_at": now, "deadline": now + 3600,
            "max_continuations": 10, "continuations": 0, "asked": False, "stopped": False}


def hook_decision(result, state, now):
    """Pure transition; the caller persists state under its lock before emitting JSON."""
    exact(state, "version started_at deadline max_continuations continuations asked stopped")
    require(state["version"] == 1)
    for key in ("started_at", "deadline", "max_continuations", "continuations"):
        require(type(state[key]) is int)
    require(0 < state["deadline"] - state["started_at"] <= 3600)
    require(0 <= state["continuations"] <= state["max_continuations"] <= 10)
    reason = result["reason"]
    if result["outcome"] == "complete":
        return {"continue": False, "stopReason": reason}, state
    if reason in STOPS:
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
        reason = "Root exit check: executable work remains. " + json.dumps(result["next"])
        return {"decision": "block", "reason": reason}, state
    state["stopped"] = True
    return {"continue": False, "stopReason": reason}, state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--declaration", required=True, help="Root's work.json")
    parser.add_argument("--parent-pane-id", required=True)
    args = parser.parse_args()
    try:
        decl = declaration(load(args.declaration))
        root = state_root()
        result = inspect(decl, owned_runs(root, identity(args.parent_pane_id), decl["task_id"]),
                         ledger_for(root, decl["task_id"]))
    except (ValueError, OSError, TypeError, KeyError, RecursionError):
        result = {"outcome": "stopped", "reason": "invalid_or_unknown_input", "next": []}
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result["outcome"] == "complete" else 2


if __name__ == "__main__":
    sys.exit(main())
