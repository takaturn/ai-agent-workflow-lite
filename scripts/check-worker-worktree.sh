#!/usr/bin/env bash
# Read-only assignment check; creation and lifecycle stay with Git/Root.
set -euo pipefail
fail() { printf 'worktree check: %s\n' "$*" >&2; exit 2; }
[[ $# == 3 ]] || fail 'usage: <worker-id> <issue-number> <absolute-worktree-path>'
worker=$1 issue=$2 path=$3
[[ $worker =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]] || fail 'invalid worker ID'
[[ $issue =~ ^[1-9][0-9]*$ ]] || fail 'invalid issue number'
[[ $path == /* && -d $path ]] || fail 'worktree path must be an existing absolute directory'
path=$(cd "$path" && pwd -P)
[[ $(basename "$path") == "issue-$issue-$worker" ]] || fail 'expected directory issue-<issue>-<worker-id>'
top=$(git -C "$path" rev-parse --show-toplevel) || fail 'not a Git worktree'
[[ $top == "$path" ]] || fail 'path must be the worktree root'
branch=$(git -C "$path" symbolic-ref --quiet --short HEAD) || fail 'detached HEAD'
[[ $branch == "feature/issue$issue" ]] || fail 'branch does not match issue'
status=$(git -C "$path" status --porcelain --untracked-files=all) || fail 'cannot inspect worktree status'
[[ -z $status ]] || fail 'worktree is dirty; inspect without resetting'
printf '%s\t%s\t%s\t%s\n' "$worker" "$issue" "$branch" "$path"
