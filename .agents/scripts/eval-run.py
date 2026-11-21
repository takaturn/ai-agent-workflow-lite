#!/usr/bin/env python3
"""Single launch path for behavioral evaluation trials (Issue #95).

init fixes a design (prompts, arms, criteria, counts) in external state; trial
launches one `claude -p` subject through launch-gate and refuses non-baseline
arms of a repro case until the baseline has reproduced; score records each
trial's verdict exactly once; status and list report runs for the Record.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from uuid import uuid4

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}\Z")
TRIAL_TIMEOUT = 480
GATE_TIMEOUT = 30
WORK_BASE = "/tmp" if os.path.isdir("/tmp") else None
RERUN_MAX = 5
RECORD = "Record this per orchestrate SKILL.md Routing 6 (the baseline did not reproduce; stop the comparison)."
FLAGS = ["--restricted", "--safe-mode", "--strict-mcp-config", "--permission-mode", "dontAsk",
         "--no-session-persistence", "--output-format", "stream-json", "--verbose",
         "--tools", "Read", "Glob", "Grep"]
GATE = Path(__file__).resolve().parent / "launch-gate.py"
# --restricted and --safe-mode still apply managed (policy) settings and their hooks.
# Linux file locations; server-delivered or other platforms' managed settings are not checked.
MANAGED = Path(os.environ.get("EVAL_RUN_MANAGED_DIR", "/etc/claude-code"))


def managed_hooks():
    """Return managed settings files that define hooks (subjects would not be hook-free)."""
    found = []
    candidates = [MANAGED / "managed-settings.json"]
    if (MANAGED / "managed-settings.d").is_dir():
        candidates += sorted((MANAGED / "managed-settings.d").glob("*.json"))
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            found.append(str(path) + " (unreadable)")
            continue
        if isinstance(data, dict) and data.get("hooks"):
            found.append(str(path))
    return found


def subject_alive(pid):
    """True if pid is still a claude subject process (guards against pid reuse)."""
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    return any(Path(arg.decode(errors="replace")).name == "claude" for arg in cmdline[:2])


def fail(message):
    raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def inside(path, root):
    path = Path(path).resolve()
    return path == root or root in path.parents


def tree_files(root):
    """Relative file paths of a version; refuse .git and symlinks anywhere."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            full = Path(dirpath) / name
            if name == ".git":
                fail(f"version contains .git: {full}; export it with git archive")
            if full.is_symlink():
                fail(f"version contains a symlink: {full}")
            if not full.is_dir() and not stat.S_ISREG(full.lstat().st_mode):
                fail(f"version contains a non-regular file: {full}")
        files.extend(str((Path(dirpath) / n).relative_to(root)) for n in filenames)
    return sorted(files)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_hashes(root):
    return {rel: file_hash(Path(root) / rel) for rel in tree_files(root)}


def digest(hashes):
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def write_atomic(path, data):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


class Task:
    def __init__(self, task_id, worktree):
        if not ID.fullmatch(task_id):
            fail("invalid task ID")
        if not Path(worktree).is_absolute():
            fail("worktree must be absolute")
        self.task_id = task_id
        self.worktree = Path(worktree).resolve()
        raw = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))
        if not Path(raw).is_absolute():
            fail("XDG_STATE_HOME must be absolute")
        self.dir = Path(raw).resolve() / "ai-agent-workflow-lite" / "eval-run" / task_id
        if inside(self.dir, self.worktree):
            fail("eval state must be outside the worktree")
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = open(self.dir / ".lock", "a+b")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail("another eval-run.py command is running for this task")
        self.lock = lock

    def runs(self):
        return sorted(p.name for p in self.dir.iterdir() if p.is_dir() and (p / "state.json").exists())

    def run_dir(self, name):
        if not ID.fullmatch(name or ""):
            fail("invalid run name")
        return self.dir / name

    def load(self, name):
        path = self.run_dir(name) / "state.json"
        if not path.exists():
            fail(f"run not initialized: {name}")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, state):
        write_atomic(self.run_dir(state["run"]) / "state.json", state)

    def gate(self, *args):
        """Run launch-gate; a timeout becomes a failed result instead of an exception."""
        command = ["python3", str(GATE), "--task-id", self.task_id, "--worktree", str(self.worktree), *args]
        try:
            return subprocess.run(command, text=True, capture_output=True, timeout=GATE_TIMEOUT, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(command, 124, "", "launch-gate timed out")

    def finish(self, trial, outcome):
        result = self.gate("finish", "--reservation-id", trial["reservation_id"], "--outcome", outcome)
        if result.returncode != 0:
            trial["finish_error"] = result.stderr.strip()[:500]

    def recover(self):
        """Settle trials left by a killed command.

        'reserving': killed around reserve; finish any reservation it got and drop the record.
        'running': killed while the subject ran; the trial becomes invalid (interrupted).
        """
        reserved = None
        for name in self.runs():
            state = self.load(name)
            changed = False
            for trial in list(state["trials"]):
                if trial["status"] == "reserving":
                    if reserved is None:
                        result = self.gate("status")
                        reserved = json.loads(result.stdout).get("reservations", []) if result.returncode == 0 else []
                    for item in reserved:
                        if item.get("run_id") == trial["run_id"] and item.get("status") == "reserved":
                            self.finish(dict(trial, reservation_id=item["id"]), "prelaunch_error")
                    state["trials"].remove(trial)
                    changed = True
                elif trial["status"] == "running":
                    # The subject runs in its own session and can outlive a killed parent: stop it first.
                    pid = trial.get("pid")
                    if pid and subject_alive(pid):
                        try:
                            os.killpg(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        trial["orphan_stopped"] = True
                    trial.update(status="interrupted", verdict="invalid", code="interrupted",
                                 reason="eval-run.py ended before the trial finished", scored_at=now())
                    self.finish(trial, "interrupted")
                    changed = True
                    print(f"recovered interrupted trial {name}/{trial['id']}", file=sys.stderr)
            if changed:
                self.save(state)


def validate_design(design, task):
    if not isinstance(design, dict) or set(design) - {"run", "model", "invalid_rerun_limit", "invalid_reasons", "cases"}:
        fail("design has unknown keys")
    if not ID.fullmatch(str(design.get("run", ""))):
        fail("design run must be a simple name")
    if not MODEL.fullmatch(str(design.get("model", ""))):
        fail("design model is required and is shared by every arm")
    limit = design.get("invalid_rerun_limit")
    if type(limit) is not int or not 0 <= limit <= RERUN_MAX:
        fail(f"invalid_rerun_limit must be an integer 0-{RERUN_MAX}")
    reasons = design.get("invalid_reasons")
    if not isinstance(reasons, dict) or not all(ID.fullmatch(k) and isinstance(v, str) and v.strip() for k, v in reasons.items()):
        fail("invalid_reasons must map reason codes to descriptions")
    if "interrupted" in reasons:
        fail("reason code 'interrupted' is reserved")
    cases = design.get("cases")
    if not isinstance(cases, list) or not cases:
        fail("design needs cases")
    seen = set()
    for case in cases:
        keys = {"id", "kind", "prompt", "criterion", "arms", "trials"}
        if not isinstance(case, dict) or not keys <= set(case) <= keys | {"repro_failures"}:
            fail("case needs id, kind, prompt, criterion, arms, trials (+ repro_failures for repro)")
        if not ID.fullmatch(str(case["id"])) or case["id"] in seen:
            fail("case id must be a unique simple name")
        seen.add(case["id"])
        if not isinstance(case["criterion"], str) or not case["criterion"].strip():
            fail(f"case {case['id']} needs a criterion")
        if type(case["trials"]) is not int or not 1 <= case["trials"] <= 20:
            fail(f"case {case['id']} trials must be 1-20")
        if case["kind"] == "repro":
            need = case.get("repro_failures")
            if type(need) is not int or not 1 <= need <= case["trials"]:
                fail(f"case {case['id']} repro_failures must be 1..trials")
        elif case["kind"] == "control":
            if "repro_failures" in case:
                fail("control cases take no repro_failures")
        else:
            fail("case kind must be repro or control")
        arms = case["arms"]
        if not isinstance(arms, dict) or "baseline" not in arms or not all(ID.fullmatch(a) for a in arms):
            fail(f"case {case['id']} arms need a baseline")
        for path in [case["prompt"], *arms.values()]:
            if not isinstance(path, str) or not Path(path).is_absolute():
                fail("prompt and arm paths must be absolute")
            if inside(path, task.worktree):
                fail(f"{path} is inside the worktree")
        prompt = Path(case["prompt"])
        if prompt.is_symlink() or not prompt.is_file():
            fail(f"prompt must be a regular file: {prompt}")
        text = prompt.read_text(encoding="utf-8")
        if not text.strip() or text.lstrip()[0] in "/!":
            fail(f"prompt must not be empty or start with / or !: {prompt}")
        for arm, path in arms.items():
            if Path(path).is_symlink() or not Path(path).is_dir():
                fail(f"arm {arm} must be a directory")
            tree_files(path)


def cmd_init(task, args):
    design_path = Path(args.design)
    if not design_path.is_absolute() or inside(design_path, task.worktree):
        fail("design must be an absolute path outside the worktree")
    design = json.loads(design_path.read_text(encoding="utf-8"))
    validate_design(design, task)
    earlier = task.runs()
    run_dir = task.run_dir(design["run"])
    if run_dir.exists():
        fail(f"run {design['run']} already fixed; use a new run name to change the design")
    run_dir.mkdir(mode=0o700)
    cases = {}
    for case in design["cases"]:
        cdir = run_dir / "cases" / case["id"]
        cdir.mkdir(parents=True)
        shutil.copyfile(case["prompt"], cdir / "prompt.txt")
        arms = {}
        for arm, src in case["arms"].items():
            shutil.copytree(src, cdir / "arms" / arm, symlinks=True)
            hashes = tree_hashes(cdir / "arms" / arm)
            arms[arm] = {"source": src, "sha256": digest(hashes), "files": hashes}
        entry = {k: case[k] for k in ("kind", "criterion", "trials")}
        entry.update(repro_failures=case.get("repro_failures"), prompt_source=case["prompt"],
                     prompt_sha256=file_hash(cdir / "prompt.txt"), arms=arms)
        cases[case["id"]] = entry
    state = {"schema_version": 1, "run": design["run"], "task_id": task.task_id, "fixed_at": now(),
             "model": design["model"], "invalid_rerun_limit": design["invalid_rerun_limit"],
             "invalid_reasons": design["invalid_reasons"], "earlier_runs": earlier,
             "cases": cases, "trials": []}
    task.save(state)
    print(f"fixed run {design['run']} (earlier runs: {', '.join(earlier) or 'none'})")
    for cid, case in cases.items():
        print(f"case {cid} ({case['kind']}): prompt sha256 {case['prompt_sha256'][:16]}")
        base = case["arms"]["baseline"]["files"]
        for arm, info in case["arms"].items():
            line = f"  arm {arm}: sha256 {info['sha256'][:16]}"
            if arm != "baseline":
                diff = sorted(p for p in set(base) | set(info["files"]) if base.get(p) != info["files"].get(p))
                line += "; differs from baseline in: " + (", ".join(diff) or "(nothing)")
            print(line)


def tally(state, cid, arm):
    rows = [t for t in state["trials"] if t["case"] == cid and t["arm"] == arm]
    count = {v: sum(t.get("verdict") == v for t in rows) for v in ("pass", "fail", "invalid")}
    count["launched"] = len(rows)
    return count


def repro_state(state, cid):
    case = state["cases"][cid]
    if case["kind"] != "repro":
        return None
    c = tally(state, cid, "baseline")
    need = case["repro_failures"]
    if c["fail"] >= need:
        return "reproduced"
    if any(t["case"] == cid and t["arm"] == "baseline" and "verdict" not in t for t in state["trials"]):
        return "pending"
    valid_left = case["trials"] - c["pass"] - c["fail"]
    launches_left = case["trials"] + state["invalid_rerun_limit"] - c["launched"]
    if c["fail"] + valid_left < need:
        return "not_reproduced"
    if c["fail"] + min(valid_left, launches_left) < need:
        return "undetermined"
    return "pending"


def parse_stream(path):
    tools, by_id, result = [], {}, None
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        content = (item.get("message") or {}).get("content") if isinstance(item.get("message"), dict) else None
        if item.get("type") == "assistant" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    entry = {"name": block.get("name"), "input": block.get("input"), "is_error": None}
                    tools.append(entry)
                    by_id[block.get("id")] = entry
        elif item.get("type") == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") in by_id:
                    by_id[block["tool_use_id"]]["is_error"] = bool(block.get("is_error"))
        elif item.get("type") == "result":
            result = item
    return tools, result


class Interrupted(BaseException):
    pass


def on_signal(signum, frame):
    raise Interrupted(signal.Signals(signum).name)


def cmd_trial(task, args):
    state = task.load(args.run)
    case = state["cases"].get(args.case)
    if case is None or args.arm not in case["arms"]:
        fail("unknown case or arm")
    pending = [t["id"] for t in state["trials"] if "verdict" not in t]
    if pending:
        fail("score the unscored trial(s) first: " + ", ".join(pending))
    if case["kind"] == "repro" and args.arm != "baseline":
        status = repro_state(state, args.case)
        if status != "reproduced":
            fail(f"case {args.case}: baseline is {status}; other arms launch only after it reproduces."
                 + (" " + RECORD if status in ("not_reproduced", "undetermined") else ""))
    count = tally(state, args.case, args.arm)
    if count["pass"] + count["fail"] >= case["trials"]:
        fail("planned valid trials already done for this case and arm")
    if count["launched"] >= case["trials"] + state["invalid_rerun_limit"]:
        fail("launch limit (trials + invalid_rerun_limit) reached for this case and arm")
    cdir = task.run_dir(args.run) / "cases" / args.case
    if file_hash(cdir / "prompt.txt") != case["prompt_sha256"]:
        fail("prompt changed after init")
    arm_dir = cdir / "arms" / args.arm
    if digest(tree_hashes(arm_dir)) != case["arms"][args.arm]["sha256"]:
        fail("arm files changed after init")
    hooks = managed_hooks()
    if hooks:
        fail("managed settings define hooks, which apply even with --restricted --safe-mode: "
             + ", ".join(hooks) + "; run trials on a host without them")
    trials_dir = task.run_dir(args.run) / "trials"
    trials_dir.mkdir(exist_ok=True)
    # The id never reuses a directory left by an earlier failed attempt.
    used = {t["id"] for t in state["trials"]} | {p.name for p in trials_dir.iterdir()}
    trial_id = next(f"t{n:03d}" for n in range(1, 10000) if f"t{n:03d}" not in used)
    tdir = trials_dir / trial_id
    tdir.mkdir()
    # A placeholder before reserving lets recover() return a slot reserved by a killed command.
    trial = {"id": trial_id, "case": args.case, "arm": args.arm, "run_id": f"{args.run}-{trial_id}",
             "started_at": now(), "status": "reserving"}
    state["trials"].append(trial)
    task.save(state)

    def abandon(message):
        state["trials"].remove(trial)
        task.save(state)
        if trial.get("reservation_id"):
            task.finish(trial, "prelaunch_error")
        fail(message)

    reserved = task.gate("reserve", "--provider", "claude", "--model", state["model"], "--path", "run-eval",
                         "--run-id", trial["run_id"])
    if reserved.returncode != 0:
        abandon("launch-gate refused; no subject launched: " + reserved.stderr.strip())
    trial.update(reservation_id=reserved.stdout.strip(), status="running")
    task.save(state)
    work = None
    try:
        # An unnamed copy in a fixed neutral temp base: no case/arm/session names in the path.
        work = Path(tempfile.mkdtemp(dir=WORK_BASE))
        shutil.copytree(arm_dir, work, dirs_exist_ok=True, symlinks=True)
        # copytree copies the arm's modes onto the 0700 mkdtemp directory; keep the copy private.
        for dirpath, dirnames, filenames in os.walk(work):
            os.chmod(dirpath, 0o700)
            for name in filenames:
                os.chmod(Path(dirpath) / name, 0o600)
        before = tree_hashes(work)
    except OSError as error:
        if work:
            shutil.rmtree(work, ignore_errors=True)
        abandon(f"could not prepare the subject's directory: {error}")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    command = ["claude", "-p", *FLAGS, "--model", state["model"]]
    started = time.monotonic()
    previous = {s: signal.signal(s, on_signal) for s in (signal.SIGTERM, signal.SIGHUP)}
    try:
        with open(cdir / "prompt.txt", "rb") as stdin, open(tdir / "stream.jsonl", "wb") as out, \
                open(tdir / "stderr.txt", "wb") as err:
            try:
                proc = subprocess.Popen(command, cwd=work, env=env, stdin=stdin, stdout=out, stderr=err,
                                        start_new_session=True)
            except OSError as error:
                shutil.rmtree(work, ignore_errors=True)
                abandon(f"subject did not start: {error}")
            trial["pid"] = proc.pid
            task.save(state)
            try:
                code = proc.wait(timeout=TRIAL_TIMEOUT)
                outcome = "returned" if code == 0 else "failed"
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                code = proc.wait()
                outcome = "timed_out"
            except BaseException as error:
                # Ctrl-C, SIGTERM or SIGHUP: stop the subject, record the trial as interrupted, re-raise.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                shutil.rmtree(work, ignore_errors=True)
                trial.update(status="interrupted", verdict="invalid", code="interrupted",
                             reason=f"eval-run.py was interrupted ({error.__class__.__name__})", scored_at=now())
                task.save(state)
                task.finish(trial, "interrupted")
                task.save(state)
                raise
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)
    seconds = round(time.monotonic() - started, 1)
    try:
        changed = tree_hashes(work) != before
    except ValueError:
        changed = True
    shutil.rmtree(work, ignore_errors=True)
    tools, result = parse_stream(tdir / "stream.jsonl")
    ok = (outcome == "returned" and result is not None and result.get("is_error") is False
          and result.get("subtype") == "success")
    # Save the outcome before returning the slot, so a failure there cannot turn it into "interrupted".
    trial.update(status=outcome, exit_code=code, seconds=seconds, completed=ok, workdir_changed=changed,
                 tools=tools, result_subtype=(result or {}).get("subtype"), finished_at=now())
    task.save(state)
    task.finish(trial, "returned" if outcome == "returned" else "failed")
    task.save(state)
    print(json.dumps({"trial": trial_id, "case": args.case, "arm": args.arm, "status": outcome,
                      "completed": ok, "workdir_changed": changed, "seconds": seconds,
                      "tools": tools, "result": (result or {}).get("result"),
                      "criterion": case["criterion"],
                      "scorable": "pass, fail or invalid" if ok else "invalid only (not completed)"},
                     indent=2, ensure_ascii=False))


def cmd_score(task, args):
    state = task.load(args.run)
    trial = next((t for t in state["trials"] if t["id"] == args.trial), None)
    if trial is None:
        fail("unknown trial")
    if "verdict" in trial:
        fail("trial already scored; scores cannot be rewritten")
    if not args.reason.strip():
        fail("a reason is required")
    if not trial.get("completed") and args.verdict != "invalid":
        fail("a trial that did not complete normally can only be invalid (execution failure, not a task failure)")
    if args.verdict == "invalid" and trial.get("completed"):
        if args.code not in state["invalid_reasons"]:
            fail("invalidating a completed trial needs a reason code fixed at init: "
                 + (", ".join(state["invalid_reasons"]) or "none were fixed"))
    if args.code and args.verdict != "invalid":
        fail("--code is only for invalid")
    trial.update(verdict=args.verdict, reason=args.reason, code=args.code, scored_at=now())
    task.save(state)
    print(f"scored {args.trial}: {args.verdict}")


def cmd_status(task, args):
    state = task.load(args.run)
    out = {"run": state["run"], "model": state["model"], "fixed_at": state["fixed_at"],
           "earlier_runs": state["earlier_runs"], "cases": {}, "invalid": []}
    for cid, case in state["cases"].items():
        out["cases"][cid] = {"kind": case["kind"], "repro": repro_state(state, cid),
                             "arms": {arm: dict(planned=case["trials"], **tally(state, cid, arm)) for arm in case["arms"]}}
    out["invalid"] = [{"trial": t["id"], "case": t["case"], "arm": t["arm"], "code": t.get("code"), "reason": t.get("reason")}
                      for t in state["trials"] if t.get("verdict") == "invalid"]
    out["unscored"] = [t["id"] for t in state["trials"] if "verdict" not in t]
    print(json.dumps(out, indent=2, ensure_ascii=False))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--worktree", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init").add_argument("--design", required=True)
    trial = sub.add_parser("trial")
    for name in ("--run", "--case", "--arm"):
        trial.add_argument(name, required=True)
    score = sub.add_parser("score")
    score.add_argument("--run", required=True)
    score.add_argument("--trial", required=True)
    score.add_argument("--verdict", choices=("pass", "fail", "invalid"), required=True)
    score.add_argument("--reason", required=True)
    score.add_argument("--code")
    sub.add_parser("status").add_argument("--run", required=True)
    sub.add_parser("list")
    args = parser.parse_args()
    try:
        task = Task(args.task_id, args.worktree)
        task.recover()
        if args.command == "list":
            for name in task.runs():
                state = task.load(name)
                print(f"{name}\tfixed {state['fixed_at']}\ttrials {len(state['trials'])}")
        else:
            {"init": cmd_init, "trial": cmd_trial, "score": cmd_score, "status": cmd_status}[args.command](task, args)
    except (ValueError, OSError, json.JSONDecodeError, UnicodeError, subprocess.TimeoutExpired) as error:
        print("eval-run: " + str(error), file=sys.stderr)
        return 2
    except (Interrupted, KeyboardInterrupt) as error:
        print(f"eval-run: interrupted ({error.__class__.__name__}); the trial is recorded as interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
