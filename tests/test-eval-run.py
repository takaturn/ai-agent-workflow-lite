#!/usr/bin/env python3
"""eval-run.py: order gate, fixed design, scoring rules and recovery (Issue #95).

A fake `claude` on PATH records how it was launched and exits at once, so no
model runs. The cases replay the #77 evaluation (R1: 4 arms x 2, C1: 2 arms x 1).
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / ".agents/scripts/eval-run.py"
GATE = ROOT / ".agents/scripts/launch-gate.py"
FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
log = Path(os.environ["FAKE_LOG"])
mode = Path(os.environ["FAKE_MODE"]).read_text().strip() if Path(os.environ["FAKE_MODE"]).exists() else "ok"
entry = {"argv": sys.argv[1:], "cwd": os.getcwd(), "stdin": sys.stdin.read(),
         "claudecode": "CLAUDECODE" in os.environ, "pid": os.getpid(),
         "mode": oct(os.stat(".").st_mode & 0o777), "agents_mode": oct(os.stat("AGENTS.md").st_mode & 0o777)}
with log.open("a") as f:
    f.write(json.dumps(entry) + "\n")
if mode == "sleep":
    time.sleep(60)
if mode == "write":
    Path("written.txt").write_text("x")
print(json.dumps({"type": "system", "subtype": "init", "tools": ["Read"]}))
print(json.dumps({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "tu1", "name": "Read", "input": {"file_path": "AGENTS.md"}}]}}))
print(json.dumps({"type": "user", "message": {"content": [
    {"type": "tool_result", "tool_use_id": "tu1", "is_error": mode == "readerr", "content": "x"}]}}))
if mode != "noresult":
    print(json.dumps({"type": "result", "subtype": "error_max_turns" if mode == "subtype" else "success", "is_error": mode == "iserror", "result": "answer"}))
sys.exit(3 if mode == "exit" else 0)
'''


class EvalRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.bin = base / "bin"
        self.bin.mkdir()
        (self.bin / "claude").write_text(FAKE)
        (self.bin / "claude").chmod(0o755)
        self.log = base / "claude.log"
        self.mode = base / "mode"
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", XDG_STATE_HOME=str(base / "state"),
                        FAKE_LOG=str(self.log), FAKE_MODE=str(self.mode), CLAUDECODE="1",
                        TMPDIR=str(base / "marker-issue95-session"), EVAL_RUN_MANAGED_DIR=str(base / "managed"))
        (base / "marker-issue95-session").mkdir()
        self.worktree = str(ROOT)
        self.src = base / "src"
        for arm in ("baseline", "a", "b", "c"):
            d = self.src / arm
            d.mkdir(parents=True)
            (d / "AGENTS.md").write_text("rules " + ("" if arm == "baseline" else arm))
            (d / "same.txt").write_text("same")
        for name in ("r1", "c1"):
            (self.src / f"{name}.txt").write_text(f"Read AGENTS.md and answer {name}.")
        self.task = "evaltask"

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, *args, ok=True):
        result = subprocess.run(["python3", str(EVAL), "--task-id", self.task, "--worktree", self.worktree, *args],
                                env=self.env, text=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
        if ok is not None:
            self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def approve(self, count, path="run-eval", ref="fixture-approval"):
        subprocess.run(["python3", str(GATE), "--task-id", self.task, "--worktree", self.worktree, "approve",
                        "--approval-ref", ref, "--provider", "claude", "--model", "haiku",
                        "--additional-launches", str(count), "--paths", path], env=self.env, check=True,
                       capture_output=True)

    def design(self, run="r", limit=1, cases=None, **extra):
        data = {"run": run, "model": "haiku", "invalid_rerun_limit": limit,
                "invalid_reasons": {"input_missing": "the prompt did not reach the subject"},
                "cases": cases if cases is not None else [
                    {"id": "R1", "kind": "repro", "prompt": str(self.src / "r1.txt"), "criterion": "reads AGENTS.md",
                     "arms": {a: str(self.src / a) for a in ("baseline", "a", "b", "c")}, "trials": 2,
                     "repro_failures": 1},
                    {"id": "C1", "kind": "control", "prompt": str(self.src / "c1.txt"), "criterion": "answers c1",
                     "arms": {a: str(self.src / a) for a in ("baseline", "a")}, "trials": 1}]}
        data.update(extra)
        path = Path(tempfile.mkstemp(dir=self.tmp.name, suffix=".json")[1])
        path.write_text(json.dumps(data))
        return str(path)

    def trial(self, case, arm, ok=True):
        return self.call("trial", "--run", "r", "--case", case, "--arm", arm, ok=ok)

    def score(self, trial, verdict, code=None, ok=True, run="r"):
        extra = ["--code", code] if code else []
        return self.call("score", "--run", run, "--trial", trial, "--verdict", verdict, "--reason", "fixture", *extra, ok=ok)

    def launches(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def status(self, run="r"):
        return json.loads(self.call("status", "--run", run).stdout)

    def test_77_replay_baseline_not_reproduced_stops_other_arms(self):
        self.approve(20)
        out = self.call("init", "--design", self.design()).stdout
        self.assertIn("arm a: sha256", out)
        self.assertIn("differs from baseline in: AGENTS.md", out)
        self.assertNotIn("same.txt", out)
        # A changed arm cannot go first; nothing launches.
        self.assertIn("baseline is pending", self.trial("R1", "a", ok=False).stderr)
        self.assertEqual(self.launches(), [])
        shown = json.loads(self.trial("R1", "baseline").stdout)
        self.assertEqual(shown["criterion"], "reads AGENTS.md")
        self.assertEqual(shown["tools"], [{"name": "Read", "input": {"file_path": "AGENTS.md"}, "is_error": False}])
        self.assertEqual(shown["status"], "returned")
        self.assertFalse(shown["workdir_changed"])
        # An unscored trial blocks the next launch.
        self.assertIn("score the unscored", self.trial("R1", "baseline", ok=False).stderr)
        self.score("t001", "pass")
        self.score("t001", "fail", ok=False)  # no rewrite
        self.trial("R1", "baseline")
        self.score("t002", "pass")
        for arm in ("a", "b", "c"):
            err = self.trial("R1", arm, ok=False).stderr
            self.assertIn("not_reproduced", err)
            self.assertIn("Routing 6", err)
        # Control case runs regardless of the baseline result.
        self.trial("C1", "baseline")
        self.score("t003", "pass")
        self.trial("C1", "a")
        self.score("t004", "fail")
        self.assertIn("planned valid trials", self.trial("C1", "a", ok=False).stderr)
        self.assertEqual(len(self.launches()), 4)  # #77 actually launched 10
        st = self.status()
        self.assertEqual(st["cases"]["R1"]["repro"], "not_reproduced")
        self.assertEqual(st["cases"]["R1"]["arms"]["baseline"], {"planned": 2, "launched": 2, "pass": 2, "fail": 0, "invalid": 0})
        self.assertIsNone(st["cases"]["C1"]["repro"])
        # How the subject was launched.
        for entry in self.launches():
            self.assertFalse(entry["claudecode"])
            for name in ("baseline", "R1", "C1", "evaltask", "state", "marker"):
                self.assertNotIn(name, entry["cwd"])
            # A fixed neutral base, not TMPDIR (which may name the session).
            self.assertEqual(str(Path(entry["cwd"]).parent), "/tmp")
            # The copy stays private although the arm has ordinary 0755/0644 modes.
            self.assertEqual((entry["mode"], entry["agents_mode"]), ("0o700", "0o600"))
            self.assertFalse(Path(entry["cwd"]).exists())
            self.assertEqual(entry["argv"][:1], ["-p"])
            for flag in ("--restricted", "--safe-mode", "--strict-mcp-config", "--no-session-persistence", "--verbose"):
                self.assertIn(flag, entry["argv"])
            argv = " ".join(entry["argv"])
            for pair in ("--permission-mode dontAsk", "--output-format stream-json", "--tools Read Glob Grep", "--model haiku"):
                self.assertIn(pair, argv)
        self.assertEqual(self.launches()[0]["stdin"], "Read AGENTS.md and answer r1.")
        self.assertEqual(self.launches()[2]["stdin"], "Read AGENTS.md and answer c1.")

    def test_reproduced_baseline_opens_other_arms(self):
        self.approve(20)
        self.call("init", "--design", self.design())
        self.trial("R1", "baseline")
        self.score("t001", "fail")
        self.trial("R1", "a")
        self.assertEqual(self.status()["cases"]["R1"]["repro"], "reproduced")

    def test_scoring_rules_and_undetermined(self):
        self.approve(20)
        self.call("init", "--design", self.design())
        for number, mode in enumerate(("exit", "noresult", "iserror"), 1):
            self.mode.write_text(mode)
            shown = json.loads(self.trial("R1", "baseline").stdout)
            self.assertFalse(shown["completed"])
            trial = f"t{number:03d}"
            self.score(trial, "pass", ok=False)
            self.score(trial, "fail", ok=False)
            if number < 3:
                self.score(trial, "invalid")
            else:
                break
        st = self.status()
        self.assertEqual(st["unscored"], ["t003"])
        self.assertEqual(st["cases"]["R1"]["repro"], "pending")  # not decided while a trial is unscored
        self.score("t003", "invalid")
        # trials 2 + limit 1 = 3 launches used with no valid result: undetermined.
        st = self.status()
        self.assertEqual(st["cases"]["R1"]["repro"], "undetermined")
        self.assertEqual(len(st["invalid"]), 3)
        self.assertIn("undetermined", self.trial("R1", "a", ok=False).stderr)
        self.assertIn("launch limit", self.trial("R1", "baseline", ok=False).stderr)
        # A completed trial needs a fixed reason code to become invalid.
        self.mode.write_text("subtype")
        self.assertFalse(json.loads(self.trial("C1", "baseline").stdout)["completed"])
        self.score("t004", "pass", ok=False)
        self.score("t004", "invalid")
        self.mode.write_text("ok")
        self.call("trial", "--run", "r", "--case", "C1", "--arm", "a")
        self.score("t005", "invalid", ok=False)
        self.score("t005", "invalid", code="made_up", ok=False)
        self.score("t005", "invalid", code="input_missing")
        self.assertEqual(self.status()["invalid"][-1]["code"], "input_missing")
        # A subject that writes to its working directory is recorded.
        self.mode.write_text("write")
        self.assertTrue(json.loads(self.trial("C1", "baseline").stdout)["workdir_changed"])

    def test_launch_gate_required(self):
        self.call("init", "--design", self.design())
        managed = Path(self.env["EVAL_RUN_MANAGED_DIR"]) / "managed-settings.d"
        managed.mkdir(parents=True)
        (managed / "50-org.json").write_text(json.dumps({"hooks": {"PreToolUse": []}, "x": 1}))
        self.assertIn("managed settings define hooks", self.trial("R1", "baseline", ok=False).stderr)
        (managed / "50-org.json").write_text(json.dumps({"permissions": {}}))
        self.assertIn("launch-gate refused", self.trial("R1", "baseline", ok=False).stderr)
        self.approve(1, path="run-agent")
        self.assertIn("launch-gate refused", self.trial("R1", "baseline", ok=False).stderr)
        self.assertEqual(self.launches(), [])
        self.approve(1, ref="eval-approval")
        self.trial("R1", "baseline")
        self.assertEqual(len(self.launches()), 1)

    def test_design_and_input_refusals(self):
        self.approve(20)
        bad = []
        (self.src / "slash.txt").write_text("/review this")
        (self.src / "bang.txt").write_text("  !ls")
        git_arm = self.src / "gitarm"
        (git_arm / ".git").mkdir(parents=True)
        link_arm = self.src / "linkarm"
        link_arm.mkdir()
        (link_arm / "x").symlink_to(self.src / "r1.txt")
        dir_link_arm = self.src / "dirlinkarm"
        dir_link_arm.mkdir()
        (dir_link_arm / "d").symlink_to(self.src / "a", target_is_directory=True)
        case = lambda **kw: [dict({"id": "R1", "kind": "repro", "prompt": str(self.src / "r1.txt"), "criterion": "c",
                                   "arms": {"baseline": str(self.src / "baseline")}, "trials": 1, "repro_failures": 1}, **kw)]
        bad.append(self.design(run="nocrit", cases=case(criterion=" ")))
        bad.append(self.design(run="slash", cases=case(prompt=str(self.src / "slash.txt"))))
        bad.append(self.design(run="bang", cases=case(prompt=str(self.src / "bang.txt"))))
        bad.append(self.design(run="git", cases=case(arms={"baseline": str(git_arm)})))
        bad.append(self.design(run="link", cases=case(arms={"baseline": str(link_arm)})))
        bad.append(self.design(run="dirlink", cases=case(arms={"baseline": str(dir_link_arm)})))
        bad.append(self.design(run="inwt", cases=case(arms={"baseline": str(ROOT / "tests")})))
        bad.append(self.design(run="nobase", cases=case(arms={"a": str(self.src / "a")})))
        bad.append(self.design(run="../up"))
        bad.append(self.design(run="limit", limit=6))
        for path in bad:
            self.call("init", "--design", path, ok=False)
        self.assertEqual(self.call("list").stdout, "")
        env = dict(self.env, XDG_STATE_HOME=str(ROOT / "tests"))
        result = subprocess.run(["python3", str(EVAL), "--task-id", self.task, "--worktree", self.worktree, "list"],
                                env=env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        # Fixed once; changes after init are detected; a second run shows the first.
        self.call("init", "--design", self.design())
        self.call("init", "--design", self.design(), ok=False)
        arm_copy = Path(self.env["XDG_STATE_HOME"]) / "ai-agent-workflow-lite/eval-run/evaltask/r/cases/R1/arms/a/AGENTS.md"
        prompt_copy = arm_copy.parents[2] / "prompt.txt"
        self.trial("R1", "baseline")
        self.score("t001", "fail")
        arm_copy.write_text("edited")
        self.assertIn("arm files changed", self.trial("R1", "a", ok=False).stderr)
        prompt_copy.write_text("edited")
        self.assertIn("prompt changed", self.trial("R1", "baseline", ok=False).stderr)
        self.assertIn("earlier runs: r", self.call("init", "--design", self.design(run="r2")).stdout)
        self.assertEqual(self.status("r2")["earlier_runs"], ["r"])
        self.assertEqual([l.split("\t")[0] for l in self.call("list").stdout.splitlines()], ["r", "r2"])

    def test_prelaunch_failure_returns_slot_and_retry_works(self):
        self.approve(3)
        self.call("init", "--design", self.design())
        # No claude anywhere on PATH (only python3), so the real CLI can never start here.
        nobin = Path(self.tmp.name) / "nobin"
        nobin.mkdir()
        (nobin / "python3").symlink_to(Path(sys.executable).resolve())
        saved = self.env
        self.env = dict(saved, PATH=str(nobin))
        self.assertIn("subject did not start", self.trial("R1", "baseline", ok=False).stderr)
        self.env = saved
        self.assertEqual(self.status()["unscored"], [])
        shown = json.loads(self.trial("R1", "baseline").stdout)
        self.assertEqual(shown["trial"], "t002")
        gate = subprocess.run(["python3", str(GATE), "--task-id", self.task, "--worktree", self.worktree, "status"],
                              env=self.env, text=True, capture_output=True, check=True)
        self.assertEqual([r["status"] for r in json.loads(gate.stdout)["reservations"]], ["prelaunch_error", "returned"])

    def start_sleeping_trial(self):
        self.approve(20)
        self.call("init", "--design", self.design())
        self.mode.write_text("sleep")
        proc = subprocess.Popen(["python3", str(EVAL), "--task-id", self.task, "--worktree", self.worktree,
                                 "trial", "--run", "r", "--case", "R1", "--arm", "baseline"], env=self.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 20
        while not self.launches() and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(self.launches())
        return proc

    def gate_statuses(self):
        gate = subprocess.run(["python3", str(GATE), "--task-id", self.task, "--worktree", self.worktree, "status"],
                              env=self.env, text=True, capture_output=True, check=True)
        return [r["status"] for r in json.loads(gate.stdout)["reservations"]]

    def test_sigterm_stops_subject_and_records_interrupted(self):
        proc = self.start_sleeping_trial()
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=20), 130)
        pid = self.launches()[0]["pid"]
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertEqual(self.status()["invalid"][0]["code"], "interrupted")
        self.assertEqual(self.gate_statuses(), ["interrupted"])

    def test_kill_is_recovered_and_commands_do_not_overlap(self):
        self.approve(20)
        self.call("init", "--design", self.design())
        self.mode.write_text("sleep")
        proc = subprocess.Popen(["python3", str(EVAL), "--task-id", self.task, "--worktree", self.worktree,
                                 "trial", "--run", "r", "--case", "R1", "--arm", "baseline"], env=self.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        state_file = Path(self.env["XDG_STATE_HOME"]) / "ai-agent-workflow-lite/eval-run/evaltask/r/state.json"
        deadline = time.time() + 20
        # Wait until eval-run.py saved the pid (the fake logs before that).
        while not (self.launches() and json.loads(state_file.read_text())["trials"][-1].get("pid")) \
                and time.time() < deadline:
            time.sleep(0.05)
        saved = json.loads(state_file.read_text())["trials"][-1]
        self.assertEqual(saved["pid"], self.launches()[0]["pid"])
        self.assertTrue(Path(saved["work"]).is_dir())
        self.assertIn("another eval-run.py command", self.call("list", ok=False).stderr)
        # A process with the pid's name but without the trial's token is not the subject.
        decoy_mode = Path(self.tmp.name) / "decoy-mode"
        decoy_mode.write_text("sleep")
        decoy = subprocess.Popen([str(self.bin / "claude"), "-p"],
                                 env=dict(self.env, FAKE_MODE=str(decoy_mode), FAKE_LOG=str(Path(self.tmp.name) / "decoy.log")),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 cwd=self.src / "baseline", start_new_session=True)
        try:
            proc.send_signal(signal.SIGKILL)
            proc.wait()
            pid = saved["pid"]
            st = self.status()  # the next command stops the orphaned subject before recording
            deadline = time.time() + 10
            while Path(f"/proc/{pid}").exists() and time.time() < deadline:
                time.sleep(0.05)
            self.assertFalse(Path(f"/proc/{pid}/environ").exists()
                             and saved["token"].encode() in Path(f"/proc/{pid}/environ").read_bytes())
            self.assertFalse(Path(saved["work"]).exists())
            self.assertEqual(st["invalid"][0]["code"], "interrupted")
            trial = json.loads(state_file.read_text())["trials"][-1]
            self.assertTrue(trial["orphan_stopped"])
            self.assertIsNone(decoy.poll())  # an unrelated claude keeps running
        finally:
            decoy.kill()
            decoy.wait()
        gate = subprocess.run(["python3", str(GATE), "--task-id", self.task, "--worktree", self.worktree, "status"],
                              env=self.env, text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(gate.stdout)["reservations"][0]["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
