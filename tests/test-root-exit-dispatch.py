#!/usr/bin/env python3
"""Model-free tests of the user-level SessionStart/Stop dispatcher."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parents[1]
SCRIPT = BASE / ".agents/scripts/root-exit-dispatch.py"


class DispatchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        self.repo = self.home / "repo"
        (self.repo / ".agents/scripts").mkdir(parents=True)
        (self.repo / ".agents/scripts/root-exit-check.py").write_text("")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.root = self.home / "state/ai-agent-workflow-lite"
        self.root.mkdir(parents=True)
        self.env = dict(os.environ, XDG_STATE_HOME=str(self.home / "state"),
                        HERDR_ENV="1", HERDR_PANE_ID="pane-a")
        self.work = self.root / "root-exit/session-a/work.json"

    def run_hook(self, mode="stop", active=False, cwd=None, env=None):
        event = dict(hook_event_name="Stop" if mode == "stop" else "SessionStart",
                     session_id="session-a", cwd=str(cwd or self.repo),
                     turn_id="turn-a", stop_hook_active=active)
        run = subprocess.run([sys.executable, "-B", str(SCRIPT), mode], input=json.dumps(event),
                             capture_output=True, text=True, env=env or self.env)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def declare(self, **fields):
        decl = dict(version=1, task_id=None, stop=None, out_of_scope="fixture", work=[])
        decl.update(fields)
        self.work.parent.mkdir(parents=True, exist_ok=True)
        self.work.write_text(json.dumps(decl))

    def child(self, **fields):
        folder = self.root / "repo0000/run.1"
        folder.mkdir(parents=True, exist_ok=True)
        summary = dict(parent_pane_id="pane-a", pane_id="child-a", run_id="run.1", status="running",
                       phase="prompt_wait", root_acceptance="pending", cleanup="retained")
        summary.update(fields)
        (folder / "summary.json").write_text(json.dumps(summary))

    def test_session_start_gives_path_and_session(self):
        context = self.run_hook("session-start")["hookSpecificOutput"]["additionalContext"]
        self.assertIn(str(self.work), context)
        self.assertIn("session-a", context)

    def test_undeclared_blocks_once_then_stops(self):
        self.assertEqual(self.run_hook()["decision"], "block")
        self.assertEqual(self.run_hook(active=True)["stopReason"], "work_not_declared")
        # A later user turn does not repeat the reminder.
        self.assertEqual(self.run_hook()["stopReason"], "work_not_declared")

    def test_declared_work_continues_then_completes(self):
        self.declare(work=[dict(id="tests", kind="root", state="ready")])
        self.assertEqual(self.run_hook()["decision"], "block")
        self.declare(work=[dict(id="tests", kind="root", state="done")])
        self.assertEqual(self.run_hook(active=True),
                         {"continue": False, "stopReason": "all_declared_checks_complete"})

    def test_uncollected_child_run_continues(self):
        self.run_hook("session-start")
        self.declare(out_of_scope="tracked by runs only")
        self.child()
        self.assertIn("collect_result", self.run_hook()["reason"])
        self.child(status="returned", phase="result_collected", root_acceptance="accepted",
                   accepted_by="pane-a", acceptance_note="ok", cleanup="closed", exit_code=0)
        self.assertEqual(self.run_hook(active=True)["continue"], False)

    def test_continuation_cap_and_new_turn_budget(self):
        self.declare(work=[dict(id="tests", kind="root", state="ready")])
        outputs = [self.run_hook(active=i > 0) for i in range(12)]
        self.assertEqual(sum("decision" in o for o in outputs), 10)
        self.assertEqual([o.get("stopReason") for o in outputs[10:]],
                         ["continuation_count_limit", "previous_stop"])
        self.assertEqual(self.run_hook()["decision"], "block")

    def test_explicit_stop_wins(self):
        self.declare(stop="explicit_stop", work=[dict(id="tests", kind="root", state="ready")])
        self.assertEqual(self.run_hook()["stopReason"], "explicit_stop")

    def test_noop_outside_scope(self):
        other = self.home / "other"
        other.mkdir()
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        self.assertEqual(self.run_hook(cwd=other), {})
        self.assertEqual(self.run_hook(env=dict(self.env, HERDR_ENV="0")), {})
        self.child(pane_id="pane-a", parent_pane_id="pane-root")
        self.assertEqual(self.run_hook(), {})
        self.assertEqual(self.run_hook("session-start"), {})

    def test_prior_session_runs_in_reused_pane_are_ignored(self):
        self.child()
        os.utime(self.root / "repo0000/run.1/summary.json", (1, 1))
        self.declare()
        self.assertEqual(self.run_hook()["continue"], False)

    def test_other_root_runs_do_not_affect(self):
        self.declare()
        self.child(parent_pane_id="pane-b", pane_id="child-b")
        self.assertEqual(self.run_hook()["continue"], False)

    def test_corrupt_inputs_fail_closed(self):
        self.work.parent.mkdir(parents=True)
        self.work.write_text("{")
        self.assertIn("invalid", self.run_hook()["reason"])
        self.assertEqual(self.run_hook(active=True)["stopReason"], "root_exit_check_error")
        self.declare()
        (self.work.parent / "continuation.json").write_text("{")
        self.run_hook()  # a new user turn replaces corrupt per-turn state
        (self.work.parent / "continuation.json").write_text("{")
        self.assertEqual(self.run_hook(active=True)["stopReason"], "root_exit_check_error")

    def test_lock_contention_does_not_complete(self):
        import fcntl
        self.declare()
        with open(self.work.parent / ".lock", "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertEqual(self.run_hook()["stopReason"], "root_exit_check_error")


if __name__ == "__main__":
    unittest.main()
