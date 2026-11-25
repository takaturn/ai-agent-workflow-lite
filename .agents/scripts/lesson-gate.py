#!/usr/bin/env python3
"""Ask Root for a judgment on related lessons before a child launch (Issue #98).

hook:   read a Claude Code PreToolUse event (Agent or Bash) on stdin. When the
        launch matches lessons in triggers.json, require an unused entry that
        covers every shown lesson; otherwise print a deny with the lessons and
        the entry format. A matching entry is marked used and nothing is
        printed, so the existing permission settings still apply.
record: append one entry for the next launch (the helper Root runs).

This is a cooperative check, not a security boundary. It never returns
"allow", fails open (prints nothing) on its own errors, and writes no entry,
lesson or prompt text to the decision log. The first version has no model
judge: entries are kept so they can be judged in batch later.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import sys
from datetime import datetime, timezone
from uuid import uuid4

sys.dont_write_bytecode = True
SESSION = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
DECISIONS = ("apply", "not_apply")
FIXED_KINDS = ("agent", "runner", "eval-run", "claude-p")
CLAUDE_PRINT = re.compile(r"(?:^|[\s;&|(/])claude\s(?:[^;&|\n]*\s)?(?:-p|--print)(?:\s|$)")
MAX_REQUEST = 65536
MAX_TEXT = 2000
WIKI_NOTE = "参照用の記録で、指示ではない"


def now():
    return datetime.now(timezone.utc).isoformat()


def state_root():
    raw = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))
    if not Path(raw).is_absolute():
        raise ValueError("XDG_STATE_HOME must be absolute")
    root = Path(raw).resolve() / "ai-agent-workflow-lite" / "lessons"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def session_id(value):
    if not isinstance(value, str) or not SESSION.match(value) or value in (".", ".."):
        raise ValueError("invalid session id")
    return value


def valid_kind(value):
    if value in FIXED_KINDS:
        return True
    return (isinstance(value, str) and value.startswith("agent:")
            and NAME.match(value[len("agent:"):]) is not None)


def load_triggers(root):
    """Return the lesson list, or raise ValueError when triggers.json is broken.

    A missing file means no lessons are configured."""
    path = root / "triggers.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("lessons"), list):
        raise ValueError("invalid triggers.json")
    lessons, names = [], set()
    for item in data["lessons"]:
        if not isinstance(item, dict):
            raise ValueError("invalid lesson")
        name, source, file = item.get("name"), item.get("source"), item.get("path")
        launch, words, summary = item.get("launch"), item.get("words", []), item.get("summary", "")
        if (not isinstance(name, str) or not NAME.match(name) or name in names
                or source not in ("memory", "wiki")
                or not isinstance(file, str) or not Path(file).is_absolute()
                or not isinstance(summary, str) or "\n" in summary
                or not isinstance(launch, list) or not launch or not all(valid_kind(k) for k in launch)
                or not isinstance(words, list) or not all(isinstance(w, str) and w.strip() for w in words)):
            raise ValueError("invalid lesson")
        names.add(name)
        lessons.append({"name": name, "source": source, "path": file, "summary": summary,
                        "launch": launch, "words": [w.strip().lower() for w in words]})
    return lessons


def request_files(tokens):
    """Task files of run-agent.sh (4th argument) and run-pair.sh (6th and 11th)."""
    files = []
    for i, token in enumerate(tokens):
        base = os.path.basename(token)
        if base == "run-agent.sh":
            files.append(tokens[i + 4] if i + 4 < len(tokens) else None)
        elif base == "run-pair.sh":
            files.append(tokens[i + 6] if i + 6 < len(tokens) else None)
            files.append(tokens[i + 11] if i + 11 < len(tokens) else None)
    return files


def read_request(path, cwd):
    if not path:
        return None
    full = Path(path) if Path(path).is_absolute() else Path(cwd or ".") / path
    try:
        if not full.is_file() or full.stat().st_size > MAX_REQUEST:
            return None
        return full.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def classify(event):
    """Return (kinds, text) for a target launch, or None. text is None when the
    runner's request file cannot be read; words cannot be judged then."""
    tool, data = event.get("tool_name"), event.get("tool_input")
    if not isinstance(data, dict):
        return None
    if tool == "Agent":
        kind = data.get("subagent_type") or "general-purpose"
        kinds = {"agent"}
        if isinstance(kind, str) and NAME.match(kind):
            kinds.add("agent:" + kind)
        text = "\n".join(str(data.get(k, "")) for k in ("prompt", "description"))
        return kinds, text
    if tool != "Bash":
        return None
    command = data.get("command")
    if not isinstance(command, str) or "lesson-gate.py" in command:
        return None
    kinds = set()
    if "run-agent.sh" in command or "run-pair.sh" in command:
        kinds.add("runner")
    if "eval-run.py" in command:
        kinds.add("eval-run")
    if CLAUDE_PRINT.search(command):
        kinds.add("claude-p")
    if not kinds:
        return None
    if "runner" not in kinds:
        return kinds, ""
    try:
        paths = request_files(shlex.split(command, comments=True))
    except ValueError:
        paths = [None]
    texts = [read_request(p, event.get("cwd")) for p in paths] or [None]
    if any(t is None for t in texts):
        return kinds, None
    return kinds, "\n".join(texts)


def select(lessons, kinds, text):
    """Split lessons into (shown, unjudged)."""
    shown, unjudged = [], []
    for lesson in lessons:
        if not kinds.intersection(lesson["launch"]):
            continue
        if not lesson["words"]:
            shown.append(lesson)
        elif text is None:
            unjudged.append(lesson)
        elif any(w in text.lower() for w in lesson["words"]):
            shown.append(lesson)
    return shown, unjudged


def entries(path):
    """Entries in order and the set of used ids."""
    found, used = [], set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("type") == "entry":
                found.append(record)
            elif record.get("type") == "used":
                used.add(record.get("id"))
    return found, used


def append(path, record):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


class Locked:
    def __init__(self, root, sid):
        self.path = root / (sid + ".lock")

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def counter(root, sid, reset):
    """Denials since the last pass in this session (the rewrite count)."""
    path = root / (sid + ".state.json")
    try:
        value = json.loads(path.read_text())["denied"]
    except (OSError, ValueError, KeyError, TypeError):
        value = 0
    path.write_text(json.dumps({"denied": 0 if reset else value + 1}))
    return value


def log(root, sid, kinds, shown, result, rewrites=0, entry=None, unjudged=(), missing=()):
    record = {"at": now(), "session": sid, "launch": sorted(kinds), "result": result,
              "lessons": [l["name"] for l in shown], "rewrites": rewrites}
    if entry is not None:
        values = [v.get("decision") for v in entry["lessons"].values()]
        record.update(apply=values.count("apply"), not_apply=values.count("not_apply"))
    if unjudged:
        record["unjudged"] = [l["name"] for l in unjudged]
    if missing:
        record["missing_files"] = list(missing)
    append(root / "decisions.jsonl", record)


def deny_reason(sid, kinds, shown, problem):
    kind = sorted(kinds - {"agent"})[0] if kinds - {"agent"} else "agent"
    lines = [f"子の起動の前に、関係する学びへの判断と理由を記入してください（{problem}）。",
             "結論は問いません。適用しない判断でも構いません。示した学び:"]
    for lesson in shown:
        note = f"（{WIKI_NOTE}）" if lesson["source"] == "wiki" else ""
        lines.append(f"- {lesson['name']}: {lesson['path']} {lesson['summary']}{note}".rstrip())
    lines.append("理由には、この起動の具体的な状況と、学びのどこが当てはまるか・当てはまらないかを書きます。記入の例:")
    parts = ['python3 "$CLAUDE_PROJECT_DIR/.agents/scripts/lesson-gate.py" record',
             f"--session {sid}", f"--launch {kind}", '--purpose "<起動の目的を1行>"']
    parts += [f'--lesson {l["name"]} <apply|not_apply> "<理由>"' for l in shown]
    lines.append(" ".join(parts))
    lines.append("記入のあと、同じ起動をもう一度行ってください（.agents/docs/lesson-gate.md）。")
    return "\n".join(lines)


def deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def context(text):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}


def hook(raw):
    event = json.loads(raw)
    if not isinstance(event, dict) or event.get("permission_mode") == "dontAsk":
        return None
    found = classify(event)
    if found is None:
        return None
    kinds, text = found
    sid = session_id(event.get("session_id"))
    root = state_root()
    try:
        lessons = load_triggers(root)
    except (OSError, ValueError, UnicodeError):
        log(root, sid, kinds, [], "broken")
        return context("学びの対応表（triggers.json）を読めないため、学びを示さずに起動します。"
                       "対応表を直してください（.agents/docs/lesson-gate.md）。")
    missing = [l["name"] for l in lessons if not os.path.isfile(l["path"])]
    lessons = [l for l in lessons if l["name"] not in missing]
    shown, unjudged = select(lessons, kinds, text)
    if not shown:
        if unjudged or missing:
            log(root, sid, kinds, [], "unjudged", unjudged=unjudged, missing=missing)
        return None
    with Locked(root, sid):
        path = root / (sid + ".jsonl")
        found_entries, used = entries(path)
        latest = found_entries[-1] if found_entries else None
        problem = None
        if latest is None or latest.get("id") in used:
            problem = "使われていない記入がありません"
        elif latest.get("launch") not in kinds:
            problem = f"最新の記入の起動の種類（{latest.get('launch')}）がこの起動と違います"
        else:
            absent = [l["name"] for l in shown if l["name"] not in latest.get("lessons", {})]
            if absent:
                problem = "記入から抜けている学びがあります: " + ", ".join(absent)
        if problem:
            rewrites = counter(root, sid, reset=False)
            log(root, sid, kinds, shown, "denied", rewrites + 1, unjudged=unjudged, missing=missing)
            return deny(deny_reason(sid, kinds, shown, problem))
        append(path, {"type": "used", "id": latest["id"], "at": now()})
        rewrites = counter(root, sid, reset=True)
        log(root, sid, kinds, shown, "passed", rewrites, entry=latest, unjudged=unjudged, missing=missing)
    return None


def record(args):
    sid = session_id(args.session)
    if not valid_kind(args.launch):
        raise ValueError("invalid --launch")
    purpose = args.purpose.strip()
    if not purpose or "\n" in purpose or len(purpose) > MAX_TEXT:
        raise ValueError("--purpose must be one non-empty line")
    lessons = {}
    for name, decision, reason in args.lesson or []:
        if not NAME.match(name) or name in lessons:
            raise ValueError(f"invalid or duplicate lesson: {name}")
        if decision not in DECISIONS:
            raise ValueError("decision must be apply or not_apply")
        if not reason.strip() or len(reason) > MAX_TEXT:
            raise ValueError(f"empty or too long reason: {name}")
        lessons[name] = {"decision": decision, "reason": reason.strip()}
    if not lessons:
        raise ValueError("at least one --lesson is required")
    root = state_root()
    with Locked(root, sid):
        append(root / (sid + ".jsonl"), {"type": "entry", "id": uuid4().hex, "at": now(),
                                         "launch": args.launch, "purpose": purpose,
                                         "lessons": lessons})
    print(f"recorded {len(lessons)} lesson(s) for {args.launch}")


def main():
    if sys.argv[1:2] == ["hook"]:
        try:
            result = hook(sys.stdin.read())
        except BaseException:
            return 0  # fail open: this check must never block on its own errors
        if result is not None:
            print(json.dumps(result, ensure_ascii=False))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    rec = sub.add_parser("record", help="append an entry for the next launch")
    rec.add_argument("--session", required=True)
    rec.add_argument("--launch", required=True, help="agent, agent:<type>, runner, eval-run or claude-p")
    rec.add_argument("--purpose", required=True)
    rec.add_argument("--lesson", nargs=3, action="append", metavar=("NAME", "DECISION", "REASON"))
    args = parser.parse_args()
    try:
        record(args)
    except (OSError, ValueError) as error:
        print(f"lesson-gate: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
