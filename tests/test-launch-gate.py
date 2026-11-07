#!/usr/bin/env python3
"""Model-free tests of task-wide launch reservations and approval history."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

GATE = Path(__file__).resolve().parents[1] / ".agents/scripts/launch-gate.py"


class LaunchGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {**os.environ, "XDG_STATE_HOME": self.tmp.name}
        self.worktree = str(Path(self.tmp.name) / "work")
        Path(self.worktree).mkdir()

    def call(self, task, *args, ok=True):
        result = subprocess.run(["python3", str(GATE), "--task-id", task,
                                 "--worktree", self.worktree, *args],
                                env=self.env, text=True, capture_output=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def approve(self, task, count, model="fixture-model", path="run-agent", reference="user-approval-fixture"):
        return self.call(task, "approve", "--approval-ref", reference,
                         "--provider", "fixture-provider", "--model", model,
                         "--additional-launches", str(count), "--paths", path)

    def reserve(self, task, run, model="fixture-model", path="run-agent", ok=True):
        return self.call(task, "reserve", "--provider", "fixture-provider",
                         "--model", model, "--path", path, "--run-id", run, ok=ok)

    def status(self, task):
        return json.loads(self.call(task, "status").stdout)

    def test_missing_invalid_and_limit(self):
        self.reserve("task-a", "first", ok=False)
        self.approve("task-a", 2)
        self.reserve("task-a", "wrong-model", model="other", ok=False)
        first = self.reserve("task-a", "run-one").stdout.strip()
        data = self.status("task-a")
        self.assertEqual(data["stop"]["reason"], None)
        self.call("task-a", "finish", "--reservation-id", first,
                  "--outcome", "failed")
        second = self.reserve("task-a", "resume-run").stdout.strip()
        data = self.status("task-a")
        self.assertEqual(data["stop"]["reason"], "limit_reached")
        self.assertEqual([r["status"] for r in data["reservations"]], ["failed", "reserved"])
        self.reserve("task-a", "third", ok=False)
        self.call("task-a", "finish", "--reservation-id", second,
                  "--outcome", "interrupted")
        self.assertEqual(len(self.status("task-a")["reservations"]), 2)
        self.approve("task-b", 1)
        self.reserve("task-b", "separate")

    def test_approved_addition_and_explicit_stop(self):
        self.approve("task-a", 1)
        self.reserve("task-a", "first")
        self.approve("task-a", 2, model="second-model", path="run-agent", reference="second-approval")
        data = self.status("task-a")
        self.assertEqual(len(data["approvals"]), 2)
        self.assertEqual(data["stop"]["reason"], None)
        self.reserve("task-a", "next", model="second-model", path="run-agent")
        self.reserve("task-a", "bad-path", model="second-model", path="run-pair", ok=False)
        self.reserve("task-a", "old-model-exhausted", ok=False)
        self.call("task-a", "stop")
        self.reserve("task-a", "stopped", ok=False)
        self.approve("task-a", 1, reference="after-stop-approval")
        self.assertEqual(self.status("task-a")["stop"]["reason"], "explicit_stop")

    def test_parallel_reservations_and_crash_count(self):
        self.approve("shared", 3)
        other_worktree = str(Path(self.tmp.name) / "other-worktree")
        Path(other_worktree).mkdir()
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            outcomes = list(pool.map(lambda n: subprocess.run(
                                         ["python3", str(GATE), "--task-id", "shared",
                                          "--worktree", self.worktree if n % 2 else other_worktree, "reserve",
                                          "--provider", "fixture-provider", "--model", "fixture-model",
                                          "--path", "run-agent", "--run-id", f"run-{n}"],
                                         env=self.env, text=True, capture_output=True), range(12)))
        self.assertEqual(sum(r.returncode == 0 for r in outcomes), 3)
        self.assertEqual(len(self.status("shared")["reservations"]), 3)
        self.assertTrue(all(r["status"] == "reserved" for r in self.status("shared")["reservations"]))
        self.reserve("shared", "after-crash", ok=False)

    def test_corrupt_state_fails_closed(self):
        self.approve("bad", 1)
        state = Path(self.tmp.name) / "ai-agent-workflow-lite/launch-gate/bad/state.json"
        state.write_text('{"schema_version":1,', encoding="utf-8")
        self.reserve("bad", "first", ok=False)
        self.call("bad", "approve", "--approval-ref", "new", "--provider", "fixture-provider",
                  "--model", "fixture-model", "--additional-launches", "1",
                  "--paths", "run-agent", ok=False)
        self.assertEqual(state.read_text(), '{"schema_version":1,')

    def test_parseable_corruption_fails_closed(self):
        for field in ("approval_at", "finished_at", "approval_index"):
            task = "corrupt-" + field
            self.approve(task, 2)
            reservation = self.reserve(task, "first").stdout.strip()
            self.call(task, "finish", "--reservation-id", reservation, "--outcome", "failed")
            state = Path(self.tmp.name) / "ai-agent-workflow-lite/launch-gate" / task / "state.json"
            data = json.loads(state.read_text())
            if field == "approval_at":
                data["approvals"][0]["at"] = "invalid"
            elif field == "finished_at":
                data["reservations"][0]["finished_at"] = ""
            else:
                data["reservations"][0]["approval_index"] = 10
            state.write_text(json.dumps(data))
            self.reserve(task, "next", ok=False)

    def test_approval_retry_is_idempotent_and_cannot_refill(self):
        self.approve("retry", 1)
        self.reserve("retry", "first")
        self.approve("retry", 1)
        data = self.status("retry")
        self.assertEqual(len(data["approvals"]), 1)
        self.assertEqual(data["stop"]["reason"], "limit_reached")
        self.reserve("retry", "unauthorized-retry", ok=False)

    def test_same_approval_event_cannot_change_parameters(self):
        self.approve("conflict", 1)
        result = self.call("conflict", "approve", "--approval-ref", "user-approval-fixture",
            "--provider", "fixture-provider", "--model", "fixture-model",
            "--additional-launches", "2", "--paths", "run-agent", ok=False)
        self.assertIn("already exists with different parameters", result.stderr)
        self.assertEqual(len(self.status("conflict")["approvals"]), 1)

    def test_concurrent_same_approval_event_is_recorded_once(self):
        command = ["python3", str(GATE), "--task-id", "same-event", "--worktree", self.worktree,
            "approve", "--approval-ref", "one-user-event", "--provider", "fixture-provider",
            "--model", "fixture-model", "--additional-launches", "1", "--paths", "run-agent"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda _: subprocess.run(command, env=self.env,
                capture_output=True, text=True), range(8)))
        self.assertTrue(all(result.returncode == 0 for result in outcomes))
        self.assertEqual(sum(json.loads(result.stdout)["approval_recorded"] for result in outcomes), 1)
        self.assertEqual(len(self.status("same-event")["approvals"]), 1)
        self.reserve("same-event", "first")
        self.reserve("same-event", "second", ok=False)

    def test_prelaunch_error_is_counted(self):
        self.approve("prelaunch", 1)
        reservation = self.reserve("prelaunch", "pane-failed").stdout.strip()
        self.call("prelaunch", "finish", "--reservation-id", reservation,
                  "--outcome", "prelaunch_error")
        self.assertEqual(self.status("prelaunch")["reservations"][0]["status"], "prelaunch_error")
        self.reserve("prelaunch", "retry", ok=False)

    def write_plan(self, task, launches, name="plan.json", mode=0o600):
        plans = Path(self.tmp.name) / "plans"
        plans.mkdir(exist_ok=True)
        path = plans / name
        raw = json.dumps({"task_id": task, "launches": [
            {"provider": "fixture-provider", "model": m, "count": c} for m, c in launches]}).encode()
        path.write_bytes(raw)
        path.chmod(mode)
        return path, hashlib.sha256(raw).hexdigest()

    def approve_plan(self, task, plan, answer=None):
        """show-plan when answer is None, else approve-plan --confirm answer."""
        extra = ["show-plan", "--plan", str(plan)] if answer is None else \
            ["approve-plan", "--plan", str(plan), "--confirm", answer]
        result = subprocess.run(["python3", str(GATE), "--task-id", task, "--worktree", self.worktree, *extra],
                                env=self.env, text=True, capture_output=True, stdin=subprocess.DEVNULL)
        if answer is None:
            return result
        return result.returncode, result.stdout + result.stderr

    def test_plan_confirmation_records_all_pairs_once(self):
        plan, digest = self.write_plan("plan", [("fixture-model", 2), ("second-model", 1)])
        result = self.approve_plan("plan", plan)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(digest, result.stdout)
        self.assertIn("--confirm " + digest[:8], result.stdout)
        self.assertIn("TOTAL: 3", result.stdout)
        self.assertFalse((Path(self.tmp.name) / "ai-agent-workflow-lite/launch-gate/plan/state.json").exists())
        for wrong in ("00000000", digest, ""):
            code, output = self.approve_plan("plan", plan, answer=wrong)
            self.assertNotEqual(code, 0, output)
        self.assertFalse((Path(self.tmp.name) / "ai-agent-workflow-lite/launch-gate/plan/state.json").exists())
        code, output = self.approve_plan("plan", plan, answer=digest[:8])
        self.assertEqual(code, 0, output)
        data = self.status("plan")
        self.assertEqual([a["reference"] for a in data["approvals"]], [digest[:16] + "-1", digest[:16] + "-2"])
        log = Path(self.tmp.name) / "ai-agent-workflow-lite/launch-gate/plan/plan-confirmations.jsonl"
        self.assertEqual(json.loads(log.read_text())["plan_sha256"], digest)
        # A restored copy of a used plan is refused before prompting.
        plan, _ = self.write_plan("plan", [("fixture-model", 2), ("second-model", 1)])
        code, output = self.approve_plan("plan", plan, answer=digest[:8])
        self.assertNotEqual(code, 0, output)
        self.assertIn("already used", output)
        # An interrupted append (no trailing newline) does not block completing the record.
        log.write_text(log.read_text() + '{"plan_sha256": "trunc')
        self.assertIn("already used", self.approve_plan("plan", plan, answer=digest[:8])[1])
        # Same pairs under a new label are a distinct plan and add allowance.
        plan2 = Path(self.tmp.name) / "plans/plan2.json"
        raw2 = json.dumps({"task_id": "plan", "label": "second", "launches": [
            {"provider": "fixture-provider", "model": "fixture-model", "count": 1}]}).encode()
        plan2.write_bytes(raw2)
        plan2.chmod(0o600)
        self.assertEqual(self.approve_plan("plan", plan2, answer=hashlib.sha256(raw2).hexdigest()[:8])[0], 0)
        # Even without the provenance log, deterministic references never double count.
        log.unlink()
        code, output = self.approve_plan("plan", plan, answer=digest[:8])
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.status("plan")["approvals"]), 3)
        self.reserve("plan", "one")
        self.reserve("plan", "two")
        self.reserve("plan", "three")
        self.reserve("plan", "four", ok=False)
        self.reserve("plan", "second", model="second-model")

    def test_plan_partial_conflict_and_limit_write_nothing(self):
        plan, digest = self.write_plan("atomic", [("fixture-model", 1), ("second-model", 1)])
        self.approve("atomic", 5, model="second-model", reference=digest[:16] + "-2")
        before = self.status("atomic")
        code, output = self.approve_plan("atomic", plan, answer=digest[:8])
        self.assertNotEqual(code, 0, output)
        self.assertEqual(self.status("atomic"), before)
        plan, digest = self.write_plan("big", [("fixture-model", 15), ("second-model", 6)], name="big.json")
        code, output = self.approve_plan("big", plan, answer=digest[:8])
        self.assertNotEqual(code, 0, output)
        self.assertIn("exceeds limit", output)
        plan, digest = self.write_plan("mode", [("fixture-model", 1)], name="mode.json", mode=0o644)
        self.assertNotEqual(self.approve_plan("mode", plan).returncode, 0)
        # Terminal control characters in a model name are rejected before display.
        plan, digest = self.write_plan("ctrl", [("fixture-model\x1b[2J", 1)], name="ctrl.json")
        result = self.approve_plan("ctrl", plan)
        self.assertNotIn("\x1b", result.stdout)
        self.assertIn("invalid plan provider/model", result.stderr)
        # A plan reached through a symlinked parent inside the worktree is rejected.
        inside = Path(self.worktree) / "plan.json"
        inside.write_bytes(plan.read_bytes().replace(b"\\u001b[2J", b""))
        inside.chmod(0o600)
        link = Path(self.tmp.name) / "plans/link"
        link.symlink_to(self.worktree)
        self.assertIn("outside the worktree", self.approve_plan("ctrl", link / "plan.json").stderr)
        # An unwritable provenance log prevents any state write.
        plan, digest = self.write_plan("log", [("fixture-model", 1)], name="log.json")
        task_dir = Path(self.tmp.name) / "ai-agent-workflow-lite/launch-gate/log"
        task_dir.mkdir(parents=True)
        log = task_dir / "plan-confirmations.jsonl"
        log.write_text("")
        log.chmod(0o400)
        code, output = self.approve_plan("log", plan, answer=digest[:8])
        self.assertNotEqual(code, 0, output)
        self.assertFalse((task_dir / "state.json").exists())

    def test_legacy_state_fixture_still_reserves(self):
        state = Path(self.tmp.name) / "ai-agent-workflow-lite/launch-gate/legacy/state.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"schema_version": 1, "task_id": "legacy",
            "unit": "reserved child launch slots", "usage": "unknown", "pricing": "unknown",
            "stop": {"reason": None, "at": None}, "reservations": [],
            "approvals": [{"at": "2026-11-01T00:00:00+00:00", "reference": "old-ref",
                "provider": "fixture-provider", "model": "fixture-model",
                "paths": ["run-agent"], "additional_launches": 1}]}))
        self.reserve("legacy", "first")
        self.reserve("legacy", "second", ok=False)


if __name__ == "__main__":
    unittest.main()
