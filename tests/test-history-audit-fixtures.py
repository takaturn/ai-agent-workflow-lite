#!/usr/bin/env python3
"""Deterministic checks for history-audit fixtures (shape, anonymization, expected verdicts).

This does not grade the Skill's judgments; the walkthrough is recorded separately.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "history-audit" / "fixtures"
VERDICTS = {"detected", "not_detected", "unknown"}
PROVENANCE = {"derived", "constructed"}
REQUIRED = {"id", "provenance", "provenance_note", "scope", "role_evidence", "rules", "events", "expected"}
FORBIDDEN = [
    (re.compile(r"/home/|/Users/|~/"), "local path"),
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"), "raw UUID"),
    (re.compile(r"https?://"), "URL"),
    (re.compile(r"@[A-Za-z0-9.-]+\.[a-z]{2,}"), "email"),
    (re.compile(r"github\.com/[^\s\"]+"), "repo reference"),
]
REQUIRED_IDS = {"p3-pr-base-misconfig", "p4-review-rounds-untracked", "ok-normal-delegation", "gap-index-missing", "self-claimed-root"}


def check(path):
    errs = []
    text = path.read_text(encoding="utf-8")
    for pat, label in FORBIDDEN:
        if pat.search(text):
            errs.append(f"{path.name}: contains {label}")
    data = json.loads(text)
    missing = REQUIRED - data.keys()
    if missing:
        errs.append(f"{path.name}: missing {sorted(missing)}")
    if data.get("id") != path.stem:
        errs.append(f"{path.name}: id mismatch")
    if data.get("provenance") not in PROVENANCE:
        errs.append(f"{path.name}: bad provenance")
    if not data.get("expected"):
        errs.append(f"{path.name}: no expected verdicts")
    for e in data.get("expected", []):
        if e.get("verdict") not in VERDICTS or not e.get("why"):
            errs.append(f"{path.name}: bad expected entry {e}")
    if data.get("role_evidence", {}).get("kind") in {"self_claim_only", "none"}:
        if any(e["verdict"] == "detected" for e in data.get("expected", [])):
            errs.append(f"{path.name}: detection without structural role evidence")
    return data.get("id"), errs


def main():
    files = sorted(ROOT.glob("*.json"))
    ids, errs = set(), []
    for f in files:
        i, e = check(f)
        ids.add(i)
        errs += e
    for i in sorted(REQUIRED_IDS - ids):
        errs.append(f"missing fixture {i}")
    for e in errs:
        print("FAIL", e)
    print(f"{len(files)} fixtures, {len(errs)} errors")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
