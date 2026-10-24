#!/usr/bin/env python3
"""Task-wide, fail-closed reservation ledger for the Herdr runner."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from datetime import datetime, timezone
from uuid import uuid4

UNIT = "reserved child launch slots"
PATHS = ("run-agent",)
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def fail(message):
    raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("duplicate task state key")
        result[key] = value
    return result


def state_home(worktree):
    raw = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))
    if not Path(raw).is_absolute():
        fail("XDG_STATE_HOME must be absolute")
    home = Path(raw).resolve()
    if worktree and (home == worktree or worktree in home.parents):
        fail("state home must be outside the worktree")
    return home / "ai-agent-workflow-lite" / "launch-gate"


def timestamp(value):
    if not isinstance(value, str) or not value:
        fail("invalid timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        fail("timestamp must include timezone")
    return parsed


def validate(data, task_id):
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int or
            data["schema_version"] != 1 or data.get("task_id") != task_id or
            data.get("unit") != UNIT):
        fail("invalid task state header")
    if set(data) != {"schema_version", "task_id", "unit", "approvals", "reservations", "stop", "usage", "pricing"}:
        fail("unexpected task state fields")
    approvals = data.get("approvals")
    reservations = data.get("reservations")
    stop = data.get("stop")
    if not isinstance(approvals, list) or not isinstance(reservations, list) or not isinstance(stop, dict):
        fail("invalid task state records")
    if data.get("usage") != "unknown" or data.get("pricing") != "unknown":
        fail("invalid usage/pricing state")
    if stop.get("reason") not in (None, "limit_reached", "explicit_stop") or not isinstance(stop.get("at"), (str, type(None))):
        fail("invalid stop state")
    if set(stop) != {"reason", "at"}:
        fail("unexpected stop state fields")
    if (stop["reason"] is None) != (stop["at"] is None):
        fail("inconsistent stop state")
    if stop["at"] is not None:
        timestamp(stop["at"])
    total = 0
    for item in approvals:
        if not isinstance(item, dict) or not isinstance(item.get("reference"), str) or not item["reference"].strip():
            fail("invalid approval reference")
        if set(item) != {"at", "reference", "provider", "model", "paths", "additional_launches"}:
            fail("unexpected approval fields")
        if not isinstance(item.get("at"), str) or not isinstance(item.get("provider"), str) or not item["provider"] or not isinstance(item.get("model"), str) or not item["model"]:
            fail("invalid approval identity")
        if type(item.get("additional_launches")) is not int or item["additional_launches"] <= 0:
            fail("invalid approval allowance")
        paths = item.get("paths")
        if (not isinstance(paths, list) or not paths or
                any(not isinstance(path, str) or path not in PATHS for path in paths) or
                len(paths) != len(set(paths))):
            fail("invalid approved paths")
        timestamp(item["at"])
        total += item["additional_launches"]
    ids = set()
    consumed = [0] * len(approvals)
    for item in reservations:
        if (not isinstance(item, dict) or not isinstance(item.get("id"), str) or
                not item["id"] or item["id"] in ids):
            fail("invalid reservation id")
        expected = {"id", "at", "status", "provider", "model", "path", "worktree", "run_id", "approval_index"}
        if item.get("status") != "reserved":
            expected.add("finished_at")
        if set(item) != expected:
            fail("unexpected reservation fields")
        ids.add(item["id"])
        if item.get("status") not in ("reserved", "returned", "failed", "interrupted", "prelaunch_error"):
            fail("invalid reservation status")
        if not all(isinstance(item.get(key), str) and item[key] for key in ("at", "provider", "model", "path", "worktree", "run_id")):
            fail("invalid reservation identity")
        index = item["approval_index"]
        if type(index) is not int or not 0 <= index < len(approvals):
            fail("invalid approval index")
        approval = approvals[index]
        if not (item["provider"] == approval["provider"] and item["model"] == approval["model"] and item["path"] in approval["paths"]):
            fail("reservation lacks matching approval")
        consumed[index] += 1
        if consumed[index] > approval["additional_launches"]:
            fail("approval allowance exceeded")
        if timestamp(item["at"]) < timestamp(approval["at"]):
            fail("reservation predates approval")
        if item["status"] != "reserved" and not isinstance(item.get("finished_at"), str):
            fail("invalid reservation outcome")
        if item["status"] != "reserved" and timestamp(item["finished_at"]) < timestamp(item["at"]):
            fail("outcome predates reservation")
    if len(reservations) > total:
        fail("reservations exceed allowance")
    if stop["reason"] == "limit_reached" and len(reservations) < total:
        fail("stale limit stop state")
    return total


def write_atomic(path, data):
    encoded = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
    fd, name = tempfile.mkstemp(prefix=".state.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run(args):
    if not ID.fullmatch(args.task_id):
        fail("invalid task ID")
    if not Path(args.worktree).is_absolute():
        fail("worktree must be absolute")
    worktree = Path(args.worktree).resolve()
    parent = state_home(worktree)
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    task_dir = parent / args.task_id
    task_dir.mkdir(exist_ok=True, mode=0o700)
    if task_dir.is_symlink() or (worktree and (task_dir.resolve() == worktree or worktree in task_dir.resolve().parents)):
        fail("task state directory must be outside the worktree")
    state_file = task_dir / "state.json"
    if state_file.is_symlink():
        fail("task state file must not be a symlink")
    with open(task_dir / ".lock", "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if state_file.exists():
            with state_file.open("r", encoding="utf-8") as stream:
                data = json.load(stream, object_pairs_hook=unique_object)
            total = validate(data, args.task_id)
        elif args.command == "approve":
            data = {"schema_version": 1, "task_id": args.task_id, "unit": UNIT,
                    "approvals": [], "reservations": [], "stop": {"reason": None, "at": None},
                    "usage": "unknown", "pricing": "unknown"}
            total = 0
        else:
            fail("approved task policy absent")
        if args.command == "approve":
            if (not args.approval_ref.strip() or not args.provider.strip() or
                    not args.model.strip() or args.additional_launches <= 0):
                fail("approval reference and positive additional allowance required")
            if len(args.paths) != len(set(args.paths)):
                fail("duplicate approved path")
            data["approvals"].append({"at": now(), "reference": args.approval_ref,
                "provider": args.provider, "model": args.model, "paths": args.paths,
                "additional_launches": args.additional_launches})
            if data["stop"]["reason"] == "limit_reached":
                data["stop"] = {"reason": None, "at": None}
            write_atomic(state_file, data)
            print(json.dumps({"task_id": args.task_id, "allowance": total + args.additional_launches,
                              "used": len(data["reservations"]), "approval_recorded": True}))
        elif args.command == "reserve":
            if data["stop"]["reason"] is not None:
                fail("task stopped: " + data["stop"]["reason"])
            if not any(args.provider == a["provider"] and args.model == a["model"] and args.path in a["paths"] for a in data["approvals"]):
                fail("provider/model/path not approved")
            if len(data["reservations"]) >= total:
                data["stop"] = {"reason": "limit_reached", "at": now()}
                write_atomic(state_file, data)
                fail("task launch limit reached")
            selected = next((i for i, approval in enumerate(data["approvals"])
                             if args.provider == approval["provider"] and args.model == approval["model"]
                             and args.path in approval["paths"]
                             and sum(r["approval_index"] == i for r in data["reservations"]) < approval["additional_launches"]), None)
            if selected is None:
                fail("matching approval allowance exhausted")
            reservation_id = str(uuid4())
            data["reservations"].append({"id": reservation_id, "at": now(), "status": "reserved",
                "provider": args.provider, "model": args.model, "path": args.path,
                "worktree": str(worktree), "run_id": args.run_id, "approval_index": selected})
            if len(data["reservations"]) == total:
                data["stop"] = {"reason": "limit_reached", "at": now()}
            write_atomic(state_file, data)
            print(reservation_id)
        elif args.command == "finish":
            item = next((r for r in data["reservations"] if r["id"] == args.reservation_id), None)
            if item is None or item["status"] != "reserved":
                fail("reservation absent or already finalized")
            item["status"] = args.outcome
            item["finished_at"] = now()
            write_atomic(state_file, data)
        elif args.command == "stop":
            data["stop"] = {"reason": "explicit_stop", "at": now()}
            write_atomic(state_file, data)
        elif args.command == "status":
            print(json.dumps(data, indent=2, sort_keys=True))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True, help="stable ID shared across worktrees and resumes")
    parser.add_argument("--worktree", required=True, help="absolute worktree path for external-state check")
    sub = parser.add_subparsers(dest="command", required=True)
    approve = sub.add_parser("approve", help="record externally obtained user approval attestation")
    approve.add_argument("--approval-ref", required=True)
    approve.add_argument("--provider", required=True)
    approve.add_argument("--model", required=True)
    approve.add_argument("--additional-launches", type=int, required=True)
    approve.add_argument("--paths", nargs="+", choices=PATHS, required=True)
    reserve = sub.add_parser("reserve")
    reserve.add_argument("--provider", required=True)
    reserve.add_argument("--model", required=True)
    reserve.add_argument("--path", choices=PATHS, required=True)
    reserve.add_argument("--run-id", required=True)
    finish = sub.add_parser("finish")
    finish.add_argument("--reservation-id", required=True)
    finish.add_argument("--outcome", choices=("returned", "failed", "interrupted", "prelaunch_error"), required=True)
    sub.add_parser("stop")
    sub.add_parser("status")
    args = parser.parse_args()
    try:
        run(args)
    except (ValueError, OSError, json.JSONDecodeError, UnicodeError) as error:
        print("launch-gate: " + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
