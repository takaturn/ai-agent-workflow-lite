#!/usr/bin/env python3
"""Write the final reply of the one child CLI session started for a run (Issue #71).

Unlike session-tokens.py (counts only), this reads message text. It writes the
final reply to --out and prints its byte count, or prints "null" and writes
nothing. A session is used only when exactly one record matches all of: its
first cwd equals --cwd, its first timestamp falls in session-tokens.py's start
window after --since, and its Root prompt starts with "Task from Root:" followed
by the task's first line (read from stdin). The reply must be complete (Codex
task_complete; Claude's last assistant record ends with end_turn), non-empty and
at most MAX_RESULT bytes. Any unreadable, oversized or malformed candidate,
or an unknown record format, makes the result null. Nothing links the child to
its record, so null is preferred over a guess.
"""
import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("session_tokens", Path(__file__).with_name("session-tokens.py"))
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)

MAX_RESULT = 32 * 1024
PREFIX = "Task from Root:\n"
PASTED = re.compile(r"\A<pasted_content(?:\s[^>]*)?>\n?(.*?)\n?</pasted_content(?:\s[^>]*)?>\Z", re.S)


def unwrap(text):
    text = text.strip()
    match = PASTED.match(text)
    return match.group(1).strip("\n") if match else text


def prompt_matches(text, first_line):
    if not isinstance(text, str) or not first_line.strip():
        return False
    body = unwrap(text)
    return body.startswith(PREFIX) and body[len(PREFIX):].split("\n", 1)[0] == first_line


def load(path):
    try:
        return list(st.records(path))
    except Exception as error:
        raise st.Ambiguous("unreadable candidate") from error


def codex_text(content, kind):
    if not isinstance(content, list):
        return None
    parts = [c.get("text") for c in content if isinstance(c, dict) and c.get("type") == kind]
    return "".join(parts) if parts and all(isinstance(p, str) for p in parts) else None


def codex(cwd, since, first_line):
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    matches = []
    for path in st.recent(home / "sessions", "**/rollout-*.jsonl", since):
        meta, prompt, complete, final, last_assistant = None, None, False, None, None
        for item in load(path):
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            kind = item.get("type")
            if meta is None and kind == "session_meta":
                meta = payload
            elif kind == "response_item" and payload.get("type") == "message":
                if payload.get("role") == "user" and prompt is None:
                    text = codex_text(payload.get("content"), "input_text")
                    if text is not None and text.startswith("Task from Root:"):
                        prompt = text
                elif payload.get("role") == "assistant":
                    text = codex_text(payload.get("content"), "output_text")
                    if text is not None:
                        last_assistant = text
            elif kind == "event_msg" and payload.get("type") == "task_complete":
                complete = True
                message = payload.get("last_agent_message")
                final = message if isinstance(message, str) else None
        started = st.epoch(meta.get("timestamp")) if meta else None
        if meta and meta.get("cwd") == cwd and st.in_window(started, since) and prompt_matches(prompt, first_line):
            matches.append((complete, final if final is not None else last_assistant))
    if len(matches) != 1 or not matches[0][0]:
        return None
    return matches[0][1]


def claude(cwd, since, first_line):
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    matches = []
    for path in st.recent(home / "projects", "*/*.jsonl", since):
        first, prompt, final, last_stop = None, None, None, None
        for item in load(path):
            if first is None and isinstance(item.get("cwd"), str) and isinstance(item.get("timestamp"), str):
                first = item
            if item.get("isSidechain") is True:
                continue
            message = item.get("message")
            if not isinstance(message, dict):
                continue
            if item.get("type") == "user" and prompt is None and not item.get("isMeta"):
                if isinstance(message.get("content"), str):
                    prompt = message["content"]
            elif item.get("type") == "assistant":
                last_stop = message.get("stop_reason")
                content = message.get("content")
                texts = [c.get("text") for c in content if isinstance(c, dict) and c.get("type") == "text"] \
                    if isinstance(content, list) else []
                # The closing record must carry the reply; never reuse an earlier mid-turn text.
                final = texts[-1] if texts and isinstance(texts[-1], str) else None
        if first is None or first["cwd"] != cwd or not st.in_window(st.epoch(first["timestamp"]), since):
            continue
        if prompt_matches(prompt, first_line):
            matches.append((last_stop == "end_turn", final))
    if len(matches) != 1 or not matches[0][0]:
        return None
    return matches[0][1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("codex", "claude"), required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--since", type=float, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        first_line = sys.stdin.read().split("\n", 1)[0]
        text = (codex if args.provider == "codex" else claude)(args.cwd, args.since, first_line)
        data = text.encode("utf-8") if isinstance(text, str) and text.strip() else None
    except Exception:  # never let extraction break the runner; null falls back to the screen read
        data = None
    if data is None or len(data) > MAX_RESULT:
        print("null")
        return 1
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    print(len(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
