#!/usr/bin/env python3
"""Print aggregate token usage of the one child CLI session started for a run.

Reads only the CLI's own local session records and prints counts, never
content. Prints "null" unless exactly one session matches the worktree and
started at or after --since, so an ambiguous or missing match is unavailable
rather than guessed.
"""
import argparse
import datetime
import json
import os
from pathlib import Path


def counter(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def records(path):
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                yield item


def epoch(text):
    if not isinstance(text, str):
        return None
    try:
        return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def recent(base, pattern, since):
    if not base.is_dir():
        return []
    found = []
    for path in base.glob(pattern):
        try:
            if path.is_file() and not path.is_symlink() and path.stat().st_mtime >= since:
                found.append(path)
        except OSError:
            continue
    return found


def codex(cwd, since):
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    matches = []
    for path in recent(home / "sessions", "**/rollout-*.jsonl", since):
        meta, usage = None, None
        for item in records(path):
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            if meta is None and item.get("type") == "session_meta":
                meta = payload
            if payload.get("type") == "token_count":
                info = payload.get("info")
                if isinstance(info, dict) and isinstance(info.get("total_token_usage"), dict):
                    usage = info["total_token_usage"]
        started = epoch(meta.get("timestamp")) if meta else None
        if meta and meta.get("cwd") == cwd and started is not None and started >= since:
            matches.append(usage)
    if len(matches) != 1 or matches[0] is None:
        return None
    u = matches[0]
    total_input, cached = counter(u.get("input_tokens")), counter(u.get("cached_input_tokens"))
    result = {
        "source": "codex-session",
        "input_tokens": None if total_input is None or cached is None or cached > total_input else total_input - cached,
        "cache_read_tokens": cached,
        "cache_write_tokens": counter(u.get("cache_write_input_tokens")),
        "output_tokens": counter(u.get("output_tokens")),
        "reasoning_tokens": counter(u.get("reasoning_output_tokens")),
        "total_tokens": counter(u.get("total_tokens")),
        "cost_usd": None,
    }
    return result if result["total_tokens"] is not None else None


def claude(cwd, since):
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    matches = []
    for path in recent(home / "projects", "*/*.jsonl", since):
        session_cwd, cost = None, None
        for item in records(path):
            if session_cwd is None and isinstance(item.get("cwd"), str):
                session_cwd = item["cwd"]
            if item.get("type") == "cost-state":
                cost = item
        if session_cwd != cwd:
            continue
        start = cost.get("startTime") if cost else None
        if counter(start) is None or start / 1000 < since:
            continue
        matches.append(cost)
    if len(matches) != 1:
        return None
    usage = matches[0].get("modelUsage")
    if not isinstance(usage, dict) or not usage:
        return None
    keys = {"input_tokens": "inputTokens", "cache_read_tokens": "cacheReadInputTokens",
            "cache_write_tokens": "cacheCreationInputTokens", "output_tokens": "outputTokens",
            "reasoning_tokens": "thinkingTokens"}
    result = {"source": "claude-session"}
    for name, key in keys.items():
        values = [counter(model.get(key)) if isinstance(model, dict) else None for model in usage.values()]
        result[name] = None if None in values else sum(values)
    parts = [result[k] for k in ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens")]
    result["total_tokens"] = None if None in parts else sum(parts)
    cost_usd = matches[0].get("totalCostUSD")
    known = matches[0].get("hasUnknownModelCost") is False
    result["cost_usd"] = cost_usd if known and isinstance(cost_usd, (int, float)) and not isinstance(cost_usd, bool) and cost_usd >= 0 else None
    return result if result["total_tokens"] is not None else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("codex", "claude"), required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--since", type=float, required=True)
    args = parser.parse_args()
    try:
        result = (codex if args.provider == "codex" else claude)(args.cwd, args.since)
    except (OSError, UnicodeDecodeError):
        result = None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
