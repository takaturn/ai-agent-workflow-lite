#!/usr/bin/env python3
"""Deterministic fixtures; no models, network or Herdr."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location(
    "root_exit", Path(__file__).resolve().parents[1] / ".agents/scripts/root-exit-check.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
SERIAL = dict(deliverables=[dict(id="d1", source="Issue item 1", worker=None)],
              decision="serial", reason="one deliverable")


def parallel(*workers):
    return dict(deliverables=[dict(id=f"d{i}", source=f"Issue item {i}", worker=w)
                              for i, w in enumerate(workers)],
                decision="parallel", reason="independent files, no shared resources")


class ExitCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        self.root = self.home / "state" / "ai-agent-workflow-lite"
        self.root.mkdir(parents=True)
        self.decl = dict(version=2, task_id="issue-15", stop=None, out_of_scope=None, work=[], plan=None)
        self.ledger = None
        self.runs = 0

    def inspect(self, pane="pane-a"):
        return checker.inspect(self.decl, checker.owned_runs(self.root, pane, self.decl["task_id"]), self.ledger)

    def child(self, parent="pane-a", **fields):
        self.runs += 1
        folder = self.root / "repo0000" / f"run.{self.runs}"
        folder.mkdir(parents=True, exist_ok=True)
        summary = dict(task_id="issue-15", parent_pane_id=parent, run_id=folder.name,
                       status="returned", phase="result_collected", root_acceptance="pending",
                       cleanup="retained", exit_code=0, pane_id=f"child-{self.runs}")
        summary.update(fields)
        (folder / "summary.json").write_text(json.dumps(summary))
        return folder / "summary.json"

    def accepted(self, **fields):
        base = dict(root_acceptance="accepted", cleanup="closed", accepted_by="pane-a",
                    acceptance_note="verified")
        base.update(fields)
        return base

    def test_empty_template_is_not_a_declaration(self):
        with self.assertRaises(ValueError):
            self.inspect()

    def test_runs_scoped_to_task_or_session_start(self):
        self.decl["out_of_scope"] = "unrelated"
        self.child(task_id="old-task")
        self.assertEqual(self.inspect()["outcome"], "complete")
        self.decl["task_id"] = None
        runs = checker.owned_runs(self.root, "pane-a", None, since=time.time() + 60)
        self.assertEqual(checker.inspect(self.decl, runs, None)["outcome"], "complete")
        runs = checker.owned_runs(self.root, "pane-a", None, since=0)
        self.assertEqual(checker.inspect(self.decl, runs, None)["outcome"], "incomplete")

    def test_out_of_scope_is_complete_but_open_runs_still_count(self):
        self.decl["out_of_scope"] = "question only"
        self.assertEqual(self.inspect()["reason"], "declared_out_of_scope")
        self.child()
        self.assertEqual(self.inspect()["outcome"], "incomplete")

    def test_ready_work_is_incomplete(self):
        self.decl.update(work=[dict(id="tests", kind="root", state="ready")], plan=SERIAL)
        result = self.inspect()
        self.assertEqual((result["outcome"], result["next"]),
                         ("incomplete", [{"work_id": "tests", "action": "root"}]))

    def test_permission_only_stops_but_independent_work_continues(self):
        self.decl.update(work=[dict(id="pr", kind="publish", state="permission")], plan=SERIAL)
        self.assertEqual(self.inspect()["reason"], "blocked_work")
        self.decl["work"].append(dict(id="tests", kind="root", state="ready"))
        self.assertEqual(self.inspect()["outcome"], "incomplete")

    def test_child_lifecycle(self):
        self.decl["out_of_scope"] = "runs only"
        path = self.child(status="running", phase="prompt_wait")
        self.assertEqual(self.inspect()["next"][0]["action"], "collect_result")
        summary = json.loads(path.read_text())
        for update, action in ((dict(status="returned", phase="result_collected"), "verify_and_accept"),
                               (dict(root_acceptance="accepted", accepted_by="pane-a",
                                     acceptance_note="ok"), "verify_cleanup")):
            summary.update(update)
            path.write_text(json.dumps(summary))
            self.assertEqual(self.inspect()["next"][0]["action"], action)
        summary["cleanup"] = "closed"
        path.write_text(json.dumps(summary))
        self.assertEqual(self.inspect()["outcome"], "complete")

    def test_failed_run_needs_resolution(self):
        self.decl["out_of_scope"] = "runs only"
        self.child(status="failed", phase="pane_created")
        self.assertEqual(self.inspect()["reason"], "blocked_work")
        self.runs = 0
        for p in self.root.glob("*/run.*/summary.json"):
            p.unlink()
        self.child(status="failed", phase="pane_created",
                   **self.accepted(failure_resolution="diagnosed and closed"))
        self.assertEqual(self.inspect()["outcome"], "complete")

    def test_other_roots_runs_are_excluded(self):
        self.decl["out_of_scope"] = "runs only"
        self.child(parent="pane-b", status="running")
        self.assertEqual(self.inspect()["outcome"], "complete")
        self.assertEqual(self.inspect("pane-b")["outcome"], "incomplete")

    def test_child_pane_detection(self):
        self.child()
        self.assertTrue(checker.is_child(self.root, "child-1"))
        self.assertFalse(checker.is_child(self.root, "pane-a"))

    def test_unreadable_summary_is_skipped(self):
        self.decl["out_of_scope"] = "runs only"
        bad = self.root / "repo0000" / "run.bad"
        bad.mkdir(parents=True)
        (bad / "summary.json").write_text("{")
        self.assertEqual(self.inspect()["outcome"], "complete")

    def test_stops_take_priority(self):
        self.decl.update(work=[dict(id="tests", kind="root", state="ready")], plan=SERIAL)
        self.decl["stop"] = "task_budget"
        self.assertEqual(self.inspect()["reason"], "task_budget")
        self.decl["stop"] = None
        self.ledger = dict(stop=dict(reason="explicit_stop", at="x"))
        self.assertEqual(self.inspect()["reason"], "explicit_stop")

    def test_launch_limit_blocks_only_child_launch(self):
        self.ledger = dict(stop=dict(reason="limit_reached", at="x"))
        self.decl.update(work=[dict(id="spawn", kind="child_launch", state="ready")], plan=SERIAL)
        self.assertEqual(self.inspect()["reason"], "blocked_work")

    def test_invalid_declarations(self):
        work = [dict(id="a", kind="root", state="ready")]
        launches = [dict(id=w, kind="child_launch", state="ready") for w in ("w1", "w2", "w3")]
        item = SERIAL["deliverables"][0]
        for change in (dict(extra=1), dict(stop="done"), dict(out_of_scope=" "),
                       dict(work=[dict(id="a", kind="x", state="ready")], plan=SERIAL),
                       dict(work=work * 2, plan=SERIAL),
                       # Version and plan combinations (Issue #63 spec 2).
                       dict(version=3), dict(version=True), dict(plan=SERIAL, out_of_scope="x"),
                       dict(work=work), dict(work=work, plan=None),
                       dict(work=work, plan=SERIAL, out_of_scope="x"),
                       dict(version=1, work=work), dict(version=1, work=work, stop="explicit_stop"),
                       dict(version=1, out_of_scope="x", plan=None),
                       dict(stop="explicit_stop"),
                       # Plan structure (spec 3).
                       dict(work=work, plan={**SERIAL, "extra": 1}),
                       dict(work=work, plan={**SERIAL, "deliverables": []}),
                       dict(work=work, plan={**SERIAL, "deliverables": [item] * 51}),
                       dict(work=work, plan={**SERIAL, "deliverables": [item, item]}),
                       dict(work=work, plan={**SERIAL, "deliverables": [{**item, "note": 1}]}),
                       dict(work=work, plan={**SERIAL, "deliverables": [{**item, "id": "a b"}]}),
                       dict(work=work, plan={**SERIAL, "deliverables": [{**item, "source": " "}]}),
                       dict(work=work, plan={**SERIAL, "reason": ""}),
                       dict(work=work, plan={**SERIAL, "decision": "auto"}),
                       dict(work=work, plan={**SERIAL, "deliverables": [{**item, "worker": "w1"}]}),
                       dict(work=launches, plan=parallel("w1", None)),
                       dict(work=launches, plan=parallel("w1", "w1")),
                       dict(work=launches, plan=parallel("w1", "w2", "w3")),
                       dict(work=launches[:1] + work, plan=parallel("w1", "a")),
                       dict(work=work, plan=parallel("w1", "w2"))):
            with self.subTest(change=change), self.assertRaises(ValueError):
                checker.inspect({**self.decl, **change}, [], None)
        v1 = {k: v for k, v in self.decl.items() if k != "plan"}
        checker.inspect({**v1, "version": 1, "out_of_scope": "x"}, [], None)
        for change in (dict(out_of_scope="x"),
                       dict(work=work, plan=SERIAL),
                       dict(work=work, plan={**SERIAL, "deliverables": [
                           dict(id=f"d{i}", source="item", worker=None) for i in range(50)]}),
                       dict(work=launches[:2], plan=parallel("w1", "w2", "w1")),
                       dict(work=work, plan=SERIAL, stop="explicit_stop")):
            with self.subTest(valid=change):
                checker.inspect({**self.decl, **change}, [], None)

    def test_hook_decision_limits(self):
        state = checker.new_state(100)
        incomplete = dict(outcome="incomplete", reason="executable_work", next=[])
        for _ in range(10):
            output, state = checker.hook_decision(incomplete, state, 101)
            self.assertEqual(output["decision"], "block")
        output, state = checker.hook_decision(incomplete, state, 101)
        self.assertEqual(output["stopReason"], "continuation_count_limit")
        output, _ = checker.hook_decision(incomplete, checker.new_state(100), 3700)
        self.assertEqual(output["stopReason"], "continuation_time_limit")
        output, _ = checker.hook_decision(dict(outcome="stopped", reason="explicit_stop", next=[]),
                                          checker.new_state(100), 101)
        self.assertEqual(output, {"continue": False, "stopReason": "explicit_stop"})

    def test_state_root_inside_git_is_rejected(self):
        repo = Path(__file__).resolve().parents[1]
        old = os.environ.get("XDG_STATE_HOME")
        try:
            os.environ["XDG_STATE_HOME"] = str(repo / "state")
            with self.assertRaises(ValueError):
                checker.state_root()
            os.environ["XDG_STATE_HOME"] = str(self.home / "state")
            self.assertEqual(checker.state_root(), self.root)
        finally:
            if old is None:
                os.environ.pop("XDG_STATE_HOME")
            else:
                os.environ["XDG_STATE_HOME"] = old

    def test_cli(self):
        self.decl["out_of_scope"] = "question only"
        path = self.home / "work.json"
        path.write_text(json.dumps(self.decl))
        env = dict(os.environ, XDG_STATE_HOME=str(self.home / "state"))
        import subprocess
        script = Path(__file__).resolve().parents[1] / ".agents/scripts/root-exit-check.py"
        run = subprocess.run([sys.executable, "-B", str(script), "--declaration", str(path),
                              "--parent-pane-id", "pane-a"], capture_output=True, text=True, env=env)
        self.assertEqual((run.returncode, json.loads(run.stdout)["outcome"]), (0, "complete"))
        path.write_text("{")
        run = subprocess.run([sys.executable, "-B", str(script), "--declaration", str(path),
                              "--parent-pane-id", "pane-a"], capture_output=True, text=True, env=env)
        self.assertEqual(run.returncode, 2)


if __name__ == "__main__":
    unittest.main()
