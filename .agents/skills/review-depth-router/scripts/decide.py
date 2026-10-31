#!/usr/bin/env python3
"""Deterministic review-depth routing from Root-supplied, evidence-backed scores.

It neither inspects the diff nor proves that evidence is true, and it grants no
review launch, merge or execution authority.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

DIMENSIONS = ("impact", "scope", "uncertainty", "verification", "recovery")
GATES = ("AUTH", "SECRETS", "EXECUTION", "DESTRUCTIVE", "SECURITY_GATE", "AUTONOMOUS_EFFECT", "MANDATED_REVIEW")
STATUSES = ("yes", "no", "unknown")


class InvalidAssessment(ValueError):
    pass


def _exact_keys(data, expected, label):
    if not isinstance(data, dict) or set(data) != set(expected):
        raise InvalidAssessment(f"{label} must contain exactly: {', '.join(expected)}")


def _evidence(value, label):
    if not isinstance(value, str) or not value.strip():
        raise InvalidAssessment(f"{label}: nonempty evidence required")


def _no_duplicates(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise InvalidAssessment("duplicate JSON key")
    return dict(pairs)


def decide(data: dict) -> dict:
    _exact_keys(data, ("diff", "dimensions", "gates"), "assessment")
    _evidence(data["diff"], "diff")
    _exact_keys(data["dimensions"], DIMENSIONS, "dimensions")
    _exact_keys(data["gates"], GATES, "gates")

    scores = {}
    for name in DIMENSIONS:
        record = data["dimensions"][name]
        _exact_keys(record, ("score", "evidence"), name)
        _evidence(record["evidence"], name)
        score = record["score"]
        if score is not None and (type(score) is not int or score not in (0, 1, 2)):
            raise InvalidAssessment(f"{name}: score must be 0, 1, 2, or null")
        scores[name] = score

    statuses = {}
    for name in GATES:
        record = data["gates"][name]
        _exact_keys(record, ("status", "evidence"), name)
        _evidence(record["evidence"], name)
        if record["status"] not in STATUSES:
            raise InvalidAssessment(f"{name}: status must be yes, no, or unknown")
        statuses[name] = record["status"]

    triggered = [name for name in GATES if statuses[name] == "yes"]
    uncertain_gates = [name for name in GATES if statuses[name] == "unknown"]
    unresolved = [name for name in DIMENSIONS if scores[name] is None]
    routing = {key: (2 if value is None else value) for key, value in scores.items()}
    upper_bound = sum(routing.values())

    if triggered or uncertain_gates:
        mode, reason = "FULL", "hard gate"
    elif routing["impact"] == 2 or routing["recovery"] == 2:
        mode, reason = "FULL", "high impact or irreversible recovery"
    elif routing["verification"] == 2 and (routing["uncertainty"] == 2 or routing["scope"] == 2):
        mode, reason = "FULL", "unverified complex or cross-boundary change"
    elif upper_bound >= 6:
        mode, reason = "FULL", "conservative total >= 6"
    elif upper_bound >= 3 or 2 in routing.values():
        mode, reason = "FOCUSED", "conservative total >= 3 or a single score of 2"
    else:
        mode, reason = "NORMAL", "low total with no overrides"

    provisional = bool(unresolved or uncertain_gates)
    return {
        "mode": mode,
        "state": "PROVISIONAL" if provisional else "FINAL",
        "observed_score": sum(value for value in scores.values() if value is not None),
        "routing_score": upper_bound,
        "triggered_gates": triggered,
        "uncertain_gates": uncertain_gates,
        "unknown_dimensions": unresolved,
        "reason": reason,
        # Only an existing repository rule can require an independent Reviewer; FULL alone does not.
        "independent_review_required": statuses["MANDATED_REVIEW"] != "no",
        "merge_approval": "NOT_GRANTED",
        "next_step": ("Resolve missing facts once; if still unavailable, stop automatic retries and report the blocker"
                      if provisional else "Perform the selected review; this is not merge approval"),
    }


def main():
    if len(sys.argv) != 2:
        print("usage: decide.py assessment.json", file=sys.stderr)
        return 2
    try:
        text = Path(sys.argv[1]).read_text(encoding="utf-8")
        assessment = json.loads(text, object_pairs_hook=_no_duplicates)
        print(json.dumps(decide(assessment), ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        print(f"invalid assessment: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
