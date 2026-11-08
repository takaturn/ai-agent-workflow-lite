#!/usr/bin/env python3
"""Deterministic fixtures for session-tokens.py and session-result.py; no models, network or real CLI homes."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / ".agents/scripts/session-tokens.py"
RESULT = SCRIPT.with_name("session-result.py")
TASK = "Review the diff.\nSecond line."
PROMPT = "Task from Root:\n" + TASK + "\nFinish with:"
CWD = "/work/tree"


def iso(epoch):
    # Keep milliseconds like real records; truncating to seconds made start-window cases flaky.
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch)) + ".%03dZ" % int((epoch % 1) * 1000)


class SessionTokens(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.since = time.time() - 60
        self.env = dict(os.environ, CODEX_HOME=str(self.home / "codex"), CLAUDE_CONFIG_DIR=str(self.home / "claude"))

    def tearDown(self):
        self.tmp.cleanup()

    def run_helper(self, provider, cwd=CWD):
        out = subprocess.run([sys.executable, "-I", str(SCRIPT), "--provider", provider, "--cwd", cwd,
                              "--since", str(self.since)], env=self.env, capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def codex_session(self, name, cwd=CWD, started=None, usage=None):
        d = self.home / "codex/sessions/2026/11/07"
        d.mkdir(parents=True, exist_ok=True)
        usage = usage or {"input_tokens": 900, "cached_input_tokens": 400, "cache_write_input_tokens": 0,
                          "output_tokens": 50, "reasoning_output_tokens": 7, "total_tokens": 950}
        lines = [{"type": "session_meta", "payload": {"timestamp": iso(started or time.time()), "cwd": cwd}},
                 {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"total_tokens": 1}}}},
                 {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": usage}}}]
        path = d / f"rollout-{name}.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n", encoding="utf-8")
        return path

    def claude_session(self, name, cwd=CWD, start_ms=None, cost=None, unknown=False, usage=None):
        d = self.home / "claude/projects" / name
        d.mkdir(parents=True, exist_ok=True)
        usage = usage or {"m": {"inputTokens": 10, "outputTokens": 20, "thinkingTokens": 5,
                                "cacheReadInputTokens": 30, "cacheCreationInputTokens": 40}}
        lines = [{"type": "user", "cwd": cwd, "message": {"content": "SECRET-PROMPT"}},
                 {"type": "cost-state", "startTime": start_ms or int(time.time() * 1000),
                  "totalCostUSD": 0.5 if cost is None else cost, "hasUnknownModelCost": unknown, "modelUsage": usage}]
        path = d / "s.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
        return path

    def test_codex_single_match_uses_last_total(self):
        self.codex_session("a")
        self.assertEqual(self.run_helper("codex"), {
            "source": "codex-session", "input_tokens": 500, "cache_read_tokens": 400, "cache_write_tokens": 0,
            "output_tokens": 50, "reasoning_tokens": 7, "total_tokens": 950, "cost_usd": None})

    def test_codex_unavailable_cases(self):
        self.assertIsNone(self.run_helper("codex"))                       # no records at all
        self.codex_session("old", started=self.since - 120)                # Root's older session
        self.codex_session("other", cwd="/elsewhere")                      # another worktree
        self.assertIsNone(self.run_helper("codex"))
        self.codex_session("a")
        self.codex_session("b")                                            # two children: ambiguous
        self.assertIsNone(self.run_helper("codex"))

    def test_codex_rejects_bad_counters(self):
        self.codex_session("a", usage={"input_tokens": 5, "cached_input_tokens": 9, "output_tokens": -1,
                                       "total_tokens": True})
        self.assertIsNone(self.run_helper("codex"))

    def test_symlinked_record_is_ignored(self):
        real = self.codex_session("a")
        link = real.with_name("rollout-link.jsonl")
        real.rename(self.home / "outside.jsonl")
        link.symlink_to(self.home / "outside.jsonl")
        self.assertIsNone(self.run_helper("codex"))

    def test_claude_single_match_sums_models_and_cost(self):
        self.claude_session("p", usage={"a": {"inputTokens": 10, "outputTokens": 20, "thinkingTokens": 5,
                                              "cacheReadInputTokens": 30, "cacheCreationInputTokens": 40},
                                        "b": {"inputTokens": 1, "outputTokens": 2, "thinkingTokens": 0,
                                              "cacheReadInputTokens": 3, "cacheCreationInputTokens": 4}})
        result = self.run_helper("claude")
        self.assertEqual(result, {"source": "claude-session", "input_tokens": 11, "cache_read_tokens": 33,
                                  "cache_write_tokens": 44, "output_tokens": 22, "reasoning_tokens": 5,
                                  "total_tokens": 110, "cost_usd": 0.5})
        self.assertNotIn("SECRET", json.dumps(result))

    def test_claude_unavailable_and_unknown_cost(self):
        self.claude_session("old", start_ms=int((self.since - 120) * 1000))
        self.assertIsNone(self.run_helper("claude"))
        self.claude_session("p", unknown=True)
        self.assertIsNone(self.run_helper("claude")["cost_usd"])
        self.claude_session("q")
        self.assertIsNone(self.run_helper("claude"))                       # ambiguous

    def test_start_must_fall_in_window(self):
        self.codex_session("early", started=self.since - 0.5)              # same second, just before the run
        self.codex_session("late", started=self.since + 121)               # outside the child start window
        self.assertIsNone(self.run_helper("codex"))
        self.codex_session("ok", started=self.since + 0.5)
        self.assertEqual(self.run_helper("codex")["total_tokens"], 950)

    def test_unsafe_candidates_make_result_null(self):
        self.codex_session("a")
        bad = self.home / "codex/sessions/2026/11/07/rollout-bad.jsonl"
        bad.write_bytes(b"\xff\xfe not utf-8\n")                        # invalid UTF-8
        self.assertIsNone(self.run_helper("codex"))
        bad.unlink()
        big = self.home / "codex/sessions/2026/11/07/rollout-big.jsonl"
        with open(big, "wb") as handle:                                   # sparse, cheap to create
            handle.truncate(65 * 1024 * 1024)
        self.assertIsNone(self.run_helper("codex"))
        big.unlink()
        if os.geteuid() != 0:
            locked = self.codex_session("locked")
            locked.chmod(0)
            self.assertIsNone(self.run_helper("codex"))
            locked.chmod(0o600)

    def test_claude_non_finite_cost_is_dropped(self):
        path = self.claude_session("p")
        path.write_text(path.read_text().replace('"totalCostUSD": 0.5', '"totalCostUSD": Infinity'), encoding="utf-8")
        self.assertIsNone(self.run_helper("claude")["cost_usd"])

    def test_claude_missing_counter_is_unavailable(self):
        self.claude_session("p", usage={"a": {"inputTokens": 10, "outputTokens": "20"}})
        self.assertIsNone(self.run_helper("claude"))


class SessionResult(unittest.TestCase):
    """Issue #71: final reply extraction; null on every doubtful match."""
    setUp, tearDown = SessionTokens.setUp, SessionTokens.tearDown
    codex_session, claude_session = SessionTokens.codex_session, SessionTokens.claude_session

    def fresh(self):
        self.tmp.cleanup()
        self.setUp()

    def run_result(self, provider, task=TASK, cwd=CWD):
        out = self.home / "out.md"
        out.unlink(missing_ok=True)
        proc = subprocess.run([sys.executable, "-I", str(RESULT), "--provider", provider, "--cwd", cwd,
                               "--since", str(self.since), "--out", str(out)],
                              input=task, env=self.env, capture_output=True, text=True)
        if proc.returncode == 0:
            self.assertEqual(oct(out.stat().st_mode & 0o777), "0o600")
            self.assertEqual(int(proc.stdout), out.stat().st_size)
            return out.read_text(encoding="utf-8")
        self.assertEqual((proc.returncode, proc.stdout.strip()), (1, "null"))
        self.assertFalse(out.exists())
        return None

    def codex_reply(self, name, prompt=PROMPT, final="result: done\nnext: none", complete=True, **kw):
        path = self.codex_session(name, **kw)
        user = lambda text: {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                                  "content": [{"type": "input_text", "text": text}]}}
        lines = [user("# AGENTS.md instructions for /work/tree"), user(prompt),
                 {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                       "content": [{"type": "output_text", "text": "fallback reply"}]}}]
        if complete:
            lines.append({"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": final}})
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("".join(json.dumps(x) + "\n" for x in lines))
        return path

    def claude_reply(self, name, prompt=PROMPT, final="result: done\nnext: none", stop="end_turn", ts=None, cwd=CWD, **kw):
        path = self.claude_session(name, cwd=cwd, **kw)
        lines = [{"type": "mode"},
                 {"type": "user", "cwd": cwd, "timestamp": iso(ts or time.time()), "isMeta": True,
                  "message": {"content": "<local-command-caveat>"}},
                 {"type": "user", "cwd": cwd, "message": {"content": [{"type": "tool_result", "content": "x"}]}},
                 {"type": "user", "cwd": cwd, "message": {"content": prompt}},
                 {"type": "assistant", "message": {"stop_reason": "tool_use", "content": [{"type": "text", "text": "working"}]}},
                 {"type": "assistant", "message": {"stop_reason": stop, "content": [{"type": "text", "text": final}]}},
                 {"type": "assistant", "isSidechain": True, "message": {"stop_reason": "end_turn",
                                                                        "content": [{"type": "text", "text": "sidechain"}]}}]
        # The child is still running: replace the fixture's cost-state record with live records only.
        path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        return path

    def test_full_reply_from_each_provider(self):
        self.codex_reply("a")
        self.assertEqual(self.run_result("codex"), "result: done\nnext: none")
        self.claude_reply("p", prompt="\n\n<pasted_content id=\"1f\">\n" + PROMPT + "\n</pasted_content id=\"1f\">\n")  # real closing tag
        self.assertEqual(self.run_result("claude"), "result: done\nnext: none")

    def test_codex_falls_back_to_last_assistant_message(self):
        self.codex_reply("a", final=None)
        self.assertEqual(self.run_result("codex"), "fallback reply")

    def test_unavailable_cases_are_null(self):
        for provider, make in (("codex", self.codex_reply), ("claude", self.claude_reply)):
            with self.subTest(provider=provider):
                self.assertIsNone(self.run_result(provider))                          # no candidate
                make("old", started=self.since - 120) if provider == "codex" else make("old", ts=self.since - 120)
                self.assertIsNone(self.run_result(provider))                          # outside the window
                make("other", prompt="Task from Root:\nAnother task.")
                self.assertIsNone(self.run_result(provider))                          # prompt mismatch
                make("cwd", cwd="/elsewhere")
                make("late", started=self.since + 121) if provider == "codex" else make("late", ts=self.since + 121)
                self.assertIsNone(self.run_result(provider))                          # other cwd / after the window
                make("a")
                self.assertIsNotNone(self.run_result(provider))
                self.assertIsNone(self.run_result(provider, task="Review"))           # line prefix is not a match
                self.assertIsNone(self.run_result(provider, task="\nReview the diff."))  # empty first line
                make("b")
                self.assertIsNone(self.run_result(provider))                          # same task rerun: ambiguous

    def test_doubtful_replies_are_null(self):
        cases = {"incomplete": dict(complete=False), "empty": dict(final="  \n"),
                 "oversized": dict(final="x" * (32 * 1024 + 1))}
        for label, kw in cases.items():
            with self.subTest(codex=label):
                self.fresh()
                self.codex_reply("a", **kw)
                self.assertIsNone(self.run_result("codex"))
        cases = {"not-end-turn": dict(stop="tool_use"), "empty": dict(final=""),
                 "oversized": dict(final="x" * (32 * 1024 + 1))}
        for label, kw in cases.items():
            with self.subTest(claude=label):
                self.fresh()
                self.claude_reply("p", **kw)
                self.assertIsNone(self.run_result("claude"))
        self.fresh()
        self.claude_reply("p", final="x" * (32 * 1024))
        self.assertEqual(len(self.run_result("claude")), 32 * 1024)              # boundary is inclusive

    def test_unreadable_or_unknown_format_is_null(self):
        self.codex_reply("a")
        bad = self.home / "codex/sessions/2026/11/07/rollout-bad.jsonl"
        bad.write_bytes(b"\xff\xfe not utf-8\n")
        self.assertIsNone(self.run_result("codex"))
        bad.unlink()
        path = self.claude_reply("p")
        path.write_text(path.read_text().replace('"content": "Task from Root:', '"text": "Task from Root:'), encoding="utf-8")
        self.assertIsNone(self.run_result("claude"))                                  # format changed


if __name__ == "__main__":
    unittest.main()
