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


def git(*args, timeout=10):
    env = dict(os.environ, GIT_NO_REPLACE_OBJECTS='1', GIT_GRAFT_FILE=os.devnull)
    return subprocess.check_output(['git', *args], stderr=subprocess.PIPE, env=env, timeout=timeout)


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
    required = {POLICY, 'scripts/policy-guard.py', 'AGENTS.md', '.codex',
                '.claude/settings.json', '.agents/skills',
                'scripts/install-gitleaks-hook.py', 'scripts/pre-push-gitleaks.py',
                'scripts/publication-check.py'}
    if not required.issubset(paths):
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
                # Python bytecode caches are never published; they must not block (#30).
                if path.suffix == '.pyc' and path.parent.name == '__pycache__':continue
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


class PolicyChange(ValueError):
    pass


def check(value, revision=None):
    if snapshot(value['paths'], revision) not in {entry['snapshot'] for entry in value['approvals']}:
        raise PolicyChange('policy change requires explicit approval')


def feature_base(remote, location, head):
    """Use the named remote's fetched base branch as the approved baseline."""
    names = git('remote').decode().splitlines()
    if remote not in names:
        raise ValueError('push destination must be a configured remote')
    urls = git('remote', 'get-url', '--push', '--all', remote).decode().splitlines()
    if len(urls) != 1 or urls[0] != location:
        raise ValueError('push destination does not match remote')
    try:
        branch = git('config', '--get', 'workflow.baseBranch').decode().strip()
    except subprocess.CalledProcessError:
        branch = None
    prefix = f'refs/remotes/{remote}/'
    if branch:
        ref = prefix + branch
        git('check-ref-format', ref)
    else:
        ref = git('symbolic-ref', '--quiet', prefix+'HEAD').decode().strip()
    if not ref.startswith(prefix) or ref == prefix+'HEAD':
        raise ValueError('invalid base branch')
    remote_ref = 'refs/heads/' + ref[len(prefix):]
    git('check-ref-format', remote_ref)
    base = git('rev-parse', '--verify', '--end-of-options', ref+'^{commit}').decode().strip()
    advertised = git('ls-remote', '--quiet', '--exit-code', '--refs',
                     location, remote_ref, timeout=60).decode('ascii').splitlines()
    if advertised != [base + '\t' + remote_ref]:
        raise ValueError('fetched base does not match destination')
    git('merge-base', head, base)
    return base


def bootstrap(remote):
    """Recreate a local cache only from the verified, accepted base policy."""
    urls = git('remote', 'get-url', '--push', '--all', remote).decode().splitlines()
    if len(urls) != 1:
        raise ValueError('base remote must have one push location')
    head = git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
    base = feature_base(remote, urls[0], head)
    paths = paths_read(json.loads(Path(POLICY).read_bytes()))
    candidate = snapshot(paths, base)
    if snapshot(paths) != candidate:
        raise ValueError('working policy differs from accepted base')
    source = Path(__file__).read_bytes()
    if source != git('show', base+':scripts/policy-guard.py'):
        raise ValueError('guard source differs from accepted base')
    state, installed = locations()
    if state.is_symlink() or installed.is_symlink():
        raise ValueError('symlink refused')
    lock_path = state.parent/'.workflow-policy.lock'
    if lock_path.is_symlink():
        raise ValueError('policy lock symlink')
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if state.exists():
            previous = load()
            if previous['paths'] != paths:
                raise ValueError('protected path migration requires review')
            approvals = previous['approvals']
        else:
            if installed.exists() and installed.read_bytes() != source:
                raise ValueError('unrecognized installed guard')
            approvals = []
        entry = {'reference':'base:'+base, 'revision':base, 'snapshot':candidate}
        matches = [old for old in approvals if old['reference'] == entry['reference']]
        if matches:
            if matches != [entry]:
                raise ValueError('base record collision')
            return
        approvals.append(entry)
        installed.parent.mkdir(parents=True, exist_ok=True)
        if not installed.exists() or installed.read_bytes() != source:
            atomic(installed, source, 0o700)
        value = {'version':1, 'paths':paths, 'guard_sha256':digest(source),
                 'approvals':approvals}
        atomic(state, canonical(value))
        if load() != value:
            raise ValueError('base bootstrap read-back failed')


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
        source = Path(__file__).read_bytes()
        if not previous and installed.exists() and installed.read_bytes() != source:
            raise ValueError('foreign installed guard')
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
        installed.parent.mkdir(parents=True, exist_ok=True)
        atomic(installed, source, 0o700)
        value = {'version':1, 'paths':paths, 'guard_sha256':digest(source),
                 'approvals':approvals}
        atomic(state, canonical(value))
        if load() != value:raise ValueError('approval read-back failed')


def verify_context(context, root, common):
    probe = subprocess.run(['git', '-C', context, 'rev-parse',
        '--path-format=absolute', '--git-common-dir'], capture_output=True,
        text=True, timeout=10, env=dict(os.environ, LC_ALL='C'))
    if probe.returncode == 0:
        if Path(probe.stdout.strip()).resolve() == common:
            actual = Path(git('-C', context, 'rev-parse',
                '--show-toplevel').decode().strip()).resolve()
            if actual != root:
                raise ValueError('hook binding belongs to another worktree')
    elif 'not a git repository' not in probe.stderr:
        raise ValueError('cannot identify operation repository')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['approve', 'bootstrap', 'check', 'identity', 'hook', 'pre-push'])
    parser.add_argument('--policy-root')
    parser.add_argument('--expected-common-dir')
    parser.add_argument('--approval-ref')
    parser.add_argument('--remote-name')
    parser.add_argument('--remote-location')
    args = parser.parse_args()
    try:
        if args.mode == 'approve':
            approve(args.approval_ref or '')
            print('Policy approval recorded and installed guard verified')
        elif args.mode == 'bootstrap':
            bootstrap(args.remote_name or 'origin')
            print('Accepted base policy installed and verified')
        else:
            if args.mode == 'hook':
                event = json.load(sys.stdin)
                if not isinstance(event, dict) or event.get('hook_event_name') != 'PreToolUse':raise ValueError('invalid event')
                startup = Path.cwd().resolve()
                if not isinstance(event.get('cwd'), str) or not Path(event['cwd']).is_absolute() or Path(event['cwd']).resolve() != Path(event['cwd']) or not Path(event['cwd']).is_dir():
                    raise ValueError('invalid event cwd')
                if args.policy_root is None and args.expected_common_dir is None:
                    args.policy_root = git('rev-parse', '--show-toplevel').decode().strip()
                    args.expected_common_dir = git(
                        'rev-parse', '--path-format=absolute',
                        '--git-common-dir').decode().strip()
                if not args.policy_root or not args.expected_common_dir:
                    raise ValueError('explicit policy binding required')
                root = Path(args.policy_root)
                common = Path(args.expected_common_dir)
                if not root.is_absolute() or root.resolve() != root or not common.is_absolute() or common.resolve() != common:
                    raise ValueError('canonical binding required')
                os.chdir(root)
                if Path(git('rev-parse', '--show-toplevel').decode().strip()).resolve() != root:
                    raise ValueError('policy root mismatch')
                if locations()[0].parent != common:
                    raise ValueError('common directory mismatch')
                for context in (str(startup), event['cwd']):
                    verify_context(context, root, common)

            value = load()
            try:
                check(value)
            except PolicyChange:
                if args.mode != 'hook':
                    raise
                print(json.dumps({'hookSpecificOutput': {
                    'hookEventName': 'PreToolUse',
                    'additionalContext': 'Protected policy changes are unapproved. Local inspection, tests and commits may continue under existing permissions. Publication and installed guard updates require human approval.'}}))
                return 0
            if args.mode == 'identity':
                print(json.dumps({'snapshot': snapshot(value['paths']),
                    'approvals': digest(locations()[0].read_bytes())}, sort_keys=True))
            if args.mode == 'pre-push':
                if not args.remote_name or not args.remote_location:
                    raise ValueError('missing destination remote')
                for line in sys.stdin:
                    _, new, remote_ref, old = line.split()
                    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', new) or not re.fullmatch('[0-9a-f]{'+str(len(new))+'}',old):
                        raise ValueError('invalid push identity')
                    if set(new) == {'0'}:continue
                    head = git('rev-parse', '--verify', new+'^{commit}').decode().strip()
                    base = feature_base(args.remote_name, args.remote_location, head)
                    outgoing = git('rev-list', head, '^'+base, '--')
                    for commit in outgoing.decode().splitlines():
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
