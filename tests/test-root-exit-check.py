#!/usr/bin/env python3
"""Deterministic fixtures; no models, network or Herdr."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import json
import os
import subprocess
import sys
import time

spec = importlib.util.spec_from_file_location(
    "root_exit", Path(__file__).resolve().parents[1] / ".agents/scripts/root-exit-check.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class ExitCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        self.cwd = self.home / "repo"
        self.cwd.mkdir()
        self.summaries = self.home / "state" / "ai-agent-workflow-lite" / hashlib.sha256(str(self.cwd).encode()).hexdigest()[:16]
        self.summaries.mkdir(parents=True)
        self.task = dict(version=1, task_id="issue-15", session_id="session-a",
                         parent_pane_id="pane-a", worktree=str(self.cwd),
                         stop=None, work=[], runs=[], worktrees=[str(self.cwd)])
        self.ledger = dict(schema_version=1, task_id="issue-15",
                           unit="reserved child launch slots", approvals=[], reservations=[],
                           stop=dict(reason=None, at=None), usage="unknown", pricing="unknown")

    def inspect(self):
        return checker.inspect(self.task, "session-a", "pane-a", str(self.cwd),
                               "issue-15", self.ledger, str(self.summaries), str(self.home / "state"))

    def child(self, status="returned", acceptance="pending"):
        stamp = "2026-10-25T00:00:00+00:00"
        self.ledger["approvals"] = [dict(at=stamp, reference="approved",
            provider="codex", model="fixture", paths=["run-agent"], additional_launches=1)]
        record = dict(id="reservation-a", at=stamp, status=status, provider="codex",
                      model="fixture", path="run-agent", worktree=str(self.cwd),
                      run_id="run.fixture", approval_index=0)
        if status != "reserved":
            record["finished_at"] = stamp
        self.ledger["reservations"] = [record]
        folder = self.summaries / "run.fixture"
        folder.mkdir()
        self.path = folder / "summary.json"
        self.summary = dict(task_id="issue-15", parent_pane_id="pane-a", run_id="run.fixture",
            reservation_id="reservation-a", worktree=str(self.cwd),
            status="running" if status == "reserved" else status,
            phase="prompt_wait" if status == "reserved" else "result_collected",
            root_acceptance=acceptance, cleanup="retained", exit_code=0,
            pane_id="child-a", agent_name="review-fixture")
        self.write_summary()
        self.task["runs"] = [dict(run_id="run.fixture", reservation_id="reservation-a",
                                 worktree=str(self.cwd), summary=str(self.path))]

    def write_summary(self):
        self.path.write_text(json.dumps(self.summary))

    def test_complete_and_optional_records(self):
        self.assertEqual(self.inspect()["outcome"], "complete")

    def test_records_and_readback(self):
        for kind in ("publish", "read_back"):
            self.task["work"] = [dict(id="record", kind=kind, state="ready")]
            self.assertEqual(self.inspect()["next"][0]["action"], kind)

    def test_permission_wait_and_independent_work(self):
        self.task["work"] = [dict(id="approval", kind="publish", state="permission")]
        self.assertEqual(self.inspect()["outcome"], "stopped")
        self.task["work"].append(dict(id="verify", kind="root", state="ready"))
        self.assertEqual(self.inspect()["outcome"], "incomplete")

    def test_stop_priority(self):
        for reason in ("explicit_stop", "task_budget"):
            self.task["stop"] = reason
            self.task["work"] = "invalid"
            self.assertEqual(self.inspect()["reason"], reason)

    def test_other_owner_and_task(self):
        for key in ("session_id", "parent_pane_id", "task_id"):
            with self.subTest(key=key):
                task = copy.deepcopy(self.task)
                self.task[key] = "other"
                with self.assertRaises(ValueError):
                    self.inspect()
                self.task = task

    def test_pending_acceptance(self):
        self.child()
        self.assertEqual(self.inspect()["next"][0]["action"], "verify_and_accept")

    def test_running_and_missing_collection(self):
        self.child("reserved")
        self.assertEqual(self.inspect()["next"][0]["action"], "collect_result")

    def test_missing_inventory_rejected(self):
        self.child()
        self.task["runs"] = []
        with self.assertRaises(ValueError):
            self.inspect()

    def test_unreserved_prelaunch_error_rejected(self):
        folder = self.summaries / "run.unreserved"
        folder.mkdir()
        (folder / "summary.json").write_text(json.dumps(dict(
            task_id="issue-15", parent_pane_id="pane-a", worktree=str(self.cwd),
            run_id="run.unreserved", status="prelaunch_error", reservation_id="")))
        with self.assertRaises(ValueError):
            self.inspect()

    def test_alternate_summary_home_rejected(self):
        alternate = self.home / "alternate" / "ai-agent-workflow-lite" / self.summaries.name
        alternate.mkdir(parents=True)
        with self.assertRaises(ValueError):
            checker.inspect(self.task, "session-a", "pane-a", str(self.cwd),
                            "issue-15", self.ledger, str(alternate), str(self.home / "state"))

    def test_wrong_summary_owner(self):
        self.child()
        self.summary["parent_pane_id"] = "other"
        self.write_summary()
        with self.assertRaises(ValueError):
            self.inspect()

    def test_accepted_requires_cleanup(self):
        self.child(acceptance="accepted")
        self.summary.update(accepted_by="pane-a", acceptance_note="Root fixture verification")
        self.write_summary()
        self.assertEqual(self.inspect()["next"][0]["action"], "verify_cleanup")
        self.summary["cleanup"] = "closed"
        self.write_summary()
        self.assertEqual(self.inspect()["outcome"], "complete")

    def test_launch_limit_does_not_block_root_work(self):
        self.child()
        self.ledger["stop"] = dict(reason="limit_reached", at="2026-10-25T00:00:00+00:00")
        self.task["work"] = [dict(id="launch", kind="child_launch", state="ready"),
                             dict(id="root", kind="root", state="ready")]
        result = self.inspect()
        self.assertEqual(result["outcome"], "incomplete")
        self.assertEqual(result["blocked"][0]["reason"], "launch_limit")

    def test_duplicate_json_rejected(self):
        path = self.home / "duplicate.json"
        path.write_text('{"version":1,"version":1}')
        with self.assertRaises(ValueError):
            checker.load(path)

    def hook_state(self):
        return dict(version=1, task_id="issue-15", session_id="session-a",
                    parent_pane_id="pane-a", worktree=str(self.cwd),
                    started_at=100, deadline=200, max_continuations=2,
                    continuations=0, stopped=False)

    def hook_event(self):
        return dict(hook_event_name="Stop", session_id="session-a", cwd=str(self.cwd),
                    turn_id="turn-a", stop_hook_active=True)

    def transition(self, state, result=None, now=150, event=None):
        return checker.hook_decision(result or dict(outcome="incomplete", reason="executable_work",
            next=[dict(work_id="verify", action="root")]), event or self.hook_event(), state,
            "session-a", "pane-a", str(self.cwd), "issue-15", now)

    def test_persistent_count_bound_across_turns(self):
        state = self.hook_state()
        for _ in range(2):
            output, state = self.transition(state)
            self.assertEqual(output["decision"], "block")
        output, state = self.transition(state)
        self.assertFalse(output["continue"])
        self.assertEqual(output["stopReason"], "continuation_count_limit")
        self.assertTrue(state["stopped"])

    def test_time_limit_and_clock_rollback(self):
        output, _ = self.transition(self.hook_state(), now=200)
        self.assertEqual(output["stopReason"], "continuation_time_limit")
        with self.assertRaises(ValueError):
            self.transition(self.hook_state(), now=99)

    def test_explicit_stop_is_sticky(self):
        output, state = self.transition(self.hook_state(), dict(outcome="stopped", reason="explicit_stop"))
        self.assertEqual(output["stopReason"], "explicit_stop")
        output, state = self.transition(state)
        self.assertEqual(output["stopReason"], "previous_stop")
        exhausted = self.hook_state()
        exhausted["continuations"] = exhausted["max_continuations"]
        output, _ = self.transition(exhausted, dict(outcome="stopped", reason="explicit_stop"), now=200)
        self.assertEqual(output["stopReason"], "explicit_stop")

    def test_wrong_hook_session_and_event(self):
        for key, value in (("session_id", "other"), ("hook_event_name", "SubagentStop"),
                           ("stop_hook_active", "true")):
            event = self.hook_event()
            event[key] = value
            with self.assertRaises(ValueError):
                self.transition(self.hook_state(), event=event)

    def test_actual_adapter_wire_and_disk_counter(self):
        state_home = self.home / "state"
        ledger_dir = state_home / "ai-agent-workflow-lite/launch-gate/issue-15"
        ledger_dir.mkdir(parents=True)
        (ledger_dir / "state.json").write_text(json.dumps(self.ledger))
        self.task["work"] = [dict(id="verify", kind="root", state="ready")]
        task_path = self.home / "task.json"
        task_path.write_text(json.dumps(self.task))
        state_path = self.home / "continuation.json"
        state = self.hook_state()
        state.update(started_at=int(time.time()), deadline=int(time.time()) + 60)
        state_path.write_text(json.dumps(state))
        state_path.chmod(0o600)
        lock_path = state_path.with_suffix(".lock")
        lock_path.touch(mode=0o600)
        cmd = [sys.executable, "-B", str(Path(checker.__file__)), "--task", str(task_path),
               "--task-id", "issue-15", "--session-id", "session-a", "--parent-pane-id", "pane-a",
               "--cwd", str(self.cwd), "--summary-home", str(self.summaries),
               "--hook", "--continuation-state", str(state_path)]
        for expected in ("block", "block", None):
            process = subprocess.run(cmd, input=json.dumps(self.hook_event()), capture_output=True,
                text=True, env={**os.environ, "XDG_STATE_HOME": str(state_home)}, timeout=5)
            self.assertEqual(process.returncode, 0, process.stderr)
            output = json.loads(process.stdout)
            self.assertEqual(output.get("decision"), expected)
        self.assertTrue(checker.load(state_path)["stopped"])
        self.assertEqual(checker.load(state_path)["continuations"], 2)

    def test_task_stop_survives_missing_ledger(self):
        self.task["stop"] = "explicit_stop"
        task_path = self.home / "task.json"
        task_path.write_text(json.dumps(self.task))
        cmd = [sys.executable, "-B", str(Path(checker.__file__)), "--task", str(task_path),
               "--task-id", "issue-15", "--session-id", "session-a", "--parent-pane-id", "pane-a",
               "--cwd", str(self.cwd), "--summary-home", str(self.summaries)]
        process = subprocess.run(cmd, capture_output=True, text=True,
            env={**os.environ, "XDG_STATE_HOME": str(self.home / "missing")}, timeout=5)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)["reason"], "explicit_stop")

    def test_malformed_hook_input_is_sticky(self):
        state_home = self.home / "state"
        ledger_dir = state_home / "ai-agent-workflow-lite/launch-gate/issue-15"
        ledger_dir.mkdir(parents=True)
        (ledger_dir / "state.json").write_text(json.dumps(self.ledger))
        self.task["work"] = [dict(id="verify", kind="root", state="ready")]
        task_path = self.home / "task.json"
        task_path.write_text(json.dumps(self.task))
        state_path = self.home / "continuation.json"
        state = self.hook_state()
        state.update(started_at=int(time.time()), deadline=int(time.time()) + 60)
        state_path.write_text(json.dumps(state))
        state_path.chmod(0o600)
        state_path.with_suffix(".lock").touch(mode=0o600)
        cmd = [sys.executable, "-B", str(Path(checker.__file__)), "--task", str(task_path),
               "--task-id", "issue-15", "--session-id", "session-a", "--parent-pane-id", "pane-a",
               "--cwd", str(self.cwd), "--summary-home", str(self.summaries),
               "--hook", "--continuation-state", str(state_path)]
        for payload in ('{"hook_event_name":', json.dumps(self.hook_event())):
            process = subprocess.run(cmd, input=payload, capture_output=True, text=True,
                env={**os.environ, "XDG_STATE_HOME": str(state_home)}, timeout=5)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertFalse(json.loads(process.stdout)["continue"])
        self.assertTrue(checker.load(state_path)["stopped"])


if __name__ == "__main__":
    unittest.main()
