#!/usr/bin/env bash
set -euo pipefail
base="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
repo="$tmp/repo"
mkdir "$repo"
git -C "$repo" init -q
git -C "$repo" -c user.name=Test -c user.email=test@example.invalid commit -qm initial --allow-empty
one="$tmp/issue-4-worker-a"
two="$tmp/issue-5-worker-b"
git -C "$repo" worktree add -qb feature/issue4 "$one"
git -C "$repo" worktree add -qb feature/issue5 "$two"
check() { bash "$base/scripts/check-worker-worktree.sh" "$@"; }
check worker-a 4 "$one" >"$tmp/assignment"
test "$(cat "$tmp/assignment")" = "worker-a$(printf '\t')4$(printf '\t')feature/issue4$(printf '\t')$one"
printf 'PASS: unique assignment\n'
reject() {
  if check "$@" >"$tmp/rejected" 2>&1; then
    printf 'FAIL: invalid assignment accepted\n' >&2; exit 1
  fi
}
reject worker-a 4 "$tmp/missing"
reject worker-b 5 "$one"
reject worker-a 04 "$one"
reject worker-a 4 "relative/path"
mkdir "$one/issue-4-worker-a"
reject worker-a 4 "$one/issue-4-worker-a"
grep -Fx 'worktree check: path must be the worktree root' "$tmp/rejected"
rmdir "$one/issue-4-worker-a"
# Git itself rejects duplicate branch assignment and existing branch creation.
if git -C "$repo" worktree add "$tmp/duplicate" feature/issue4 >"$tmp/git-error" 2>&1; then
  printf 'FAIL: checked-out branch assigned twice\n' >&2; exit 1
fi
if git -C "$repo" worktree add -b feature/issue4 "$tmp/conflict" >"$tmp/git-error" 2>&1; then
  printf 'FAIL: existing branch overwritten\n' >&2; exit 1
fi
check worker-b 5 "$two"
printf 'one\n' >"$one/one.txt"
reject worker-a 4 "$one"
test "$(cat "$one/one.txt")" = one
printf 'two\n' >"$two/two.txt"
test "$(git -C "$one" status --porcelain)" = '?? one.txt'
test "$(git -C "$two" status --porcelain)" = '?? two.txt'
test "$(git -C "$one" branch --show-current)" = feature/issue4
test "$(git -C "$two" branch --show-current)" = feature/issue5
git -C "$repo" worktree list --porcelain >"$tmp/worktrees"
grep -Fx "worktree $one" "$tmp/worktrees"
grep -Fx "worktree $two" "$tmp/worktrees"
# A failed worker process leaves the other environment and its edits intact.
if (cd "$one" && exit 17); then exit 1; fi
test "$(cat "$two/two.txt")" = two
git -C "$two" -c user.name=Test -c user.email=test@example.invalid add two.txt
git -C "$two" -c user.name=Test -c user.email=test@example.invalid commit -qm 'worker b continues'
test "$(git -C "$one" status --porcelain)" = '?? one.txt'
test ! -e "$one/two.txt"
test ! -e "$two/one.txt"
check worker-b 5 "$two"
printf 'PASS: refusals, independent edits/branches, failure isolation\n'
# Tracked edits and detached HEAD are also unsafe for fresh assignment.
printf 'changed\n' >>"$two/two.txt"
reject worker-b 5 "$two"
test "$(cat "$two/two.txt")" = $'two\nchanged'
git -C "$one" checkout --detach -q
reject worker-a 4 "$one"
test "$(cat "$one/one.txt")" = one
printf 'PASS: tracked dirt and detached HEAD refused without altering edits\n'
# Corrupt only the disposable fixture's index: inspection must fail closed.
index=$(git -C "$two" rev-parse --path-format=absolute --git-path index)
printf 'broken\n' >"$index"
if git -C "$two" status --porcelain >"$tmp/status" 2>"$tmp/status-error"; then
  printf 'FAIL: corrupt-index fixture did not fail\n' >&2; exit 1
fi
reject worker-b 5 "$two"
grep -Fx 'worktree check: cannot inspect worktree status' "$tmp/rejected"
printf 'PASS: failed Git status refuses assignment\n'
