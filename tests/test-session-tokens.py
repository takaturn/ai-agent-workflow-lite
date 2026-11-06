#!/usr/bin/env python3
"""Deterministic fixtures for session-tokens.py; no models, network or real CLI homes."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / ".agents/scripts/session-tokens.py"
CWD = "/work/tree"


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


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

    def test_claude_missing_counter_is_unavailable(self):
        self.claude_session("p", usage={"a": {"inputTokens": 10, "outputTokens": "20"}})
        self.assertIsNone(self.run_helper("claude"))


if __name__ == "__main__":
    unittest.main()
