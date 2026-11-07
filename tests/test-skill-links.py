#!/usr/bin/env python3
"""Check that every .agents/skills/<name>/SKILL.md has a matching relative
symlink .claude/skills/<name> -> ../../.agents/skills/<name>, and that
.claude/skills has nothing else (including broken symlinks). Issue #58."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def check(root: Path) -> list[str]:
    errors = []
    src = root / ".agents" / "skills"
    dst = root / ".claude" / "skills"
    skills = {p.parent.name for p in src.glob("*/SKILL.md")}
    entries = {p.name for p in dst.iterdir()} if dst.is_dir() else set()
    for name in sorted(skills - entries):
        errors.append(f"missing: .claude/skills/{name}")
    for name in sorted(entries - skills):
        errors.append(f"extra: .claude/skills/{name}")
    for name in sorted(skills & entries):
        link = dst / name
        if not link.is_symlink():
            errors.append(f"not a symlink: .claude/skills/{name}")
            continue
        target = os.readlink(link)
        if target != f"../../.agents/skills/{name}":
            errors.append(f"wrong target: .claude/skills/{name} -> {target}")
        elif link.resolve() != (src / name).resolve():
            errors.append(f"does not resolve: .claude/skills/{name}")
    return errors


def make_fixture(tmp: Path, names: list[str]) -> Path:
    for n in names:
        (tmp / ".agents/skills" / n).mkdir(parents=True)
        (tmp / ".agents/skills" / n / "SKILL.md").write_text("x\n")
    (tmp / ".claude/skills").mkdir(parents=True)
    for n in names:
        os.symlink(f"../../.agents/skills/{n}", tmp / ".claude/skills" / n)
    return tmp


def self_test() -> None:
    cases = {
        "ok": (lambda t: None, ""),
        "missing": (lambda t: (t / ".claude/skills/a").unlink(), "missing: .claude/skills/a"),
        "extra": (lambda t: os.symlink("../../.agents/skills/a", t / ".claude/skills/zz"), "extra: .claude/skills/zz"),
        "broken": (lambda t: os.symlink("../../.agents/skills/gone", t / ".claude/skills/gone"), "extra: .claude/skills/gone"),
        "absolute": (lambda t: ((t / ".claude/skills/a").unlink(), os.symlink(t / ".agents/skills/a", t / ".claude/skills/a")), "wrong target: .claude/skills/a"),
        "wrong-name": (lambda t: ((t / ".claude/skills/a").unlink(), os.symlink("../../.agents/skills/b", t / ".claude/skills/a")), "wrong target: .claude/skills/a"),
        "copy": (lambda t: ((t / ".claude/skills/a").unlink(), (t / ".claude/skills/a").mkdir()), "not a symlink: .claude/skills/a"),
        "dir-without-skill-md-ignored": (lambda t: (t / ".agents/skills/notes").mkdir(), ""),
    }
    for label, (mutate, expected) in cases.items():
        with tempfile.TemporaryDirectory() as d:
            t = make_fixture(Path(d), ["a", "b"])
            mutate(t)
            errs = check(t)
            if expected:
                assert any(e.startswith(expected) for e in errs), (label, errs)
            else:
                assert errs == [], (label, errs)
        print(f"ok self-test {label}")


def main() -> int:
    self_test()
    errors = check(ROOT)
    for e in errors:
        print(f"FAIL {e}")
    if errors:
        return 1
    print("ok repository skill links")
    return 0


if __name__ == "__main__":
    sys.exit(main())
