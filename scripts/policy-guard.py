#!/usr/bin/env python3
"""Cooperative policy integrity guard. Approval requires prior user authorization."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

POLICY = 'config/publication-policy.json'


def git(*args):
    env = dict(os.environ, GIT_NO_REPLACE_OBJECTS='1', GIT_GRAFT_FILE=os.devnull)
    return subprocess.check_output(['git', *args], stderr=subprocess.PIPE, env=env, timeout=10)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def locations():
    common = Path(git('rev-parse', '--git-common-dir').decode().strip()).resolve()
    return common/'workflow-policy-approvals.json', common/'hooks/workflow-policy-guard.py'


def paths_read(value):
    if set(value) != {'version', 'protected_paths'} or value['version'] != 1:
        raise ValueError('invalid policy')
    paths = value['protected_paths']
    if not isinstance(paths, list) or not paths or len(paths) != len(set(paths)):
        raise ValueError('invalid protected paths')
    for name in paths:
        if not isinstance(name, str) or not name or Path(name).is_absolute() or any(p in ('..', '.') for p in name.split('/')):
            raise ValueError('invalid protected path')
    if POLICY not in paths or 'scripts/policy-guard.py' not in paths or 'AGENTS.md' not in paths or '.codex' not in paths or '.claude/settings.json' not in paths or '.agents/skills' not in paths:
        raise ValueError('guard must protect its configuration')
    return paths


def snapshot(paths, revision=None):
    entries = {}
    if revision:
        revision = git('rev-parse', '--verify', '--end-of-options', revision+'^{commit}').decode().strip()
        for row in git('ls-tree', '-r', '-z', '--full-tree', revision, '--', *paths).split(b'\0'):
            if not row:continue
            fields, name = row.split(b'\t', 1)
            mode, kind, oid = fields.split()
            if kind != b'blob' or mode not in (b'100644', b'100755'):
                raise ValueError('unsupported protected entry')
            entries[name.decode()] = [mode.decode(), digest(git('cat-file', 'blob', oid.decode()))]
    else:
        root = Path(git('rev-parse', '--show-toplevel').decode().strip())
        for name in paths:
            candidate = root/name
            if any(parent.is_symlink() for parent in (candidate, *candidate.parents)):
                raise ValueError('protected symlink')
            found = list(candidate.rglob('*')) if candidate.is_dir() else [candidate]
            for path in found:
                if path.is_symlink():raise ValueError('protected symlink')
                if not path.exists():continue
                if path.is_dir():continue
                if not path.is_file():raise ValueError('unsupported protected entry')
                mode = '100755' if path.stat().st_mode & 0o111 else '100644'
                entries[str(path.relative_to(root))] = [mode, digest(path.read_bytes())]
    return digest(canonical(entries))


def atomic(path, raw, mode=0o600):
    fd, temporary = tempfile.mkstemp(prefix='.policy-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fchmod(stream.fileno(), mode); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def load():
    state, installed = locations()
    if state.is_symlink() or installed.is_symlink():raise ValueError('symlink refused')
    value = json.loads(state.read_bytes())
    paths_read({'version':1, 'protected_paths':value['paths']})
    if value['version'] != 1 or digest(installed.read_bytes()) != value['guard_sha256']:
        raise ValueError('installed guard changed')
    return value


def check(value, revision=None):
    if snapshot(value['paths'], revision) not in {entry['snapshot'] for entry in value['approvals']}:
        raise ValueError('policy change requires explicit approval')


def approve(reference):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', reference):
        raise ValueError('use a secret-free approval reference')
    paths = paths_read(json.loads(Path(POLICY).read_bytes()))
    state, installed = locations()
    if state.is_symlink() or installed.is_symlink():raise ValueError('symlink refused')
    with (state.parent/'.workflow-policy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = load() if state.exists() else None
        if previous and previous['paths'] != paths:
            raise ValueError('protected path changes require manual reviewed migration')
        if not previous and installed.exists():raise ValueError('foreign installed guard')
        head = git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
        candidate = snapshot(paths, head)
        if snapshot(paths) != candidate:raise ValueError('commit the exact policy before approving')
        entry = {'reference':reference, 'revision':head, 'snapshot':candidate}
        approvals = previous['approvals'] if previous else []
        matches = [old for old in approvals if old['reference'] == reference]
        if matches:
            if matches != [entry]:raise ValueError('approval reference already used')
            return
        approvals.append(entry)
        source = Path(__file__).read_bytes()
        installed.parent.mkdir(parents=True, exist_ok=True)
        atomic(installed, source, 0o700)
        value = {'version':1, 'paths':paths, 'guard_sha256':digest(source), 'approvals':approvals}
        atomic(state, canonical(value))
        if load() != value:raise ValueError('approval read-back failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['approve', 'check', 'hook', 'pre-push'])
    parser.add_argument('--approval-ref')
    args = parser.parse_args()
    try:
        if args.mode == 'approve':
            approve(args.approval_ref or '')
            print('Policy approval recorded and installed guard verified')
        else:
            if args.mode == 'hook':
                event = json.load(sys.stdin)
                if not isinstance(event, dict) or event.get('hook_event_name') != 'PreToolUse':raise ValueError('invalid event')
                os.chdir(event['cwd'])
            value = load()
            check(value)
            if args.mode == 'pre-push':
                for line in sys.stdin:
                    _, new, _, old = line.split()
                    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', new) or not re.fullmatch('[0-9a-f]{'+str(len(new))+'}',old):
                        raise ValueError('invalid push identity')
                    if set(new) == {'0'}:continue
                    head = git('rev-parse', '--verify', new+'^{commit}').decode().strip()
                    try:
                        base = git('rev-parse', '--verify', old+'^{commit}').decode().strip()
                    except subprocess.CalledProcessError:base = None
                    for commit in git('rev-list', base+'..'+head if base else head, '--').decode().splitlines():
                        check(value, commit)
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        reason = 'Policy guard blocked: approval missing, changed policy, or guard error; inspect before continuing.'
        if args.mode == 'hook':
            print(json.dumps({'hookSpecificOutput':{'hookEventName':'PreToolUse', 'permissionDecision':'deny', 'permissionDecisionReason':reason}}))
            return 0
        print(reason, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
