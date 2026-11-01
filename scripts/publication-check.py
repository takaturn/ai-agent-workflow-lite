#!/usr/bin/env python3
"""Cooperative publication gate. Raw CLI calls bypass this wrapper."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


def digest(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(args):
    files = []
    payloads = []
    for raw in args.file:
        path = Path(raw).resolve(strict=True)
        data = path.read_bytes()
        # Rich/binary artifacts require a separate content-review workflow.
        if b'\x00' in data:
            raise ValueError('binary publication input unsupported')
        data.decode('utf-8')
        files.append({'path': str(path), 'sha256': digest(data)})
        payloads.append(data)
    revision = None
    if args.git_range:
        # Include each outgoing commit (including subsequently removed content),
        # commit messages and file modes, not only the final tree diff.
        if args.git_range.startswith('-') or args.git_range.count('..') != 1 or '...' in args.git_range:
            raise ValueError('explicit base..head range required')
        endpoints = args.git_range.split('..')
        if any(not endpoint or endpoint.startswith('-') for endpoint in endpoints):
            raise ValueError('two revision endpoints required')
        git_env = dict(os.environ, GIT_NO_REPLACE_OBJECTS='1', GIT_GRAFT_FILE=os.devnull)
        commits = [subprocess.check_output(['git', 'rev-parse', '--verify',
                    '--end-of-options', endpoint+'^{commit}'], stderr=subprocess.PIPE, env=git_env)
                    .decode('ascii').strip() for endpoint in endpoints]
        resolved_range = commits[0]+'..'+commits[1]
        data = subprocess.check_output(['git', '-c', 'color.ui=false', '-c', 'core.quotePath=true', 'log',
            '--no-ext-diff', '--no-textconv', '--format=fuller', '--binary', '-p',
            '--diff-merges=first-parent', '--no-renames', resolved_range, '--'],
            stderr=subprocess.PIPE, env=git_env)
        raw_objects = []
        outgoing = subprocess.check_output(['git', 'rev-list', resolved_range, '--'],
                                          stderr=subprocess.PIPE, env=git_env)
        for oid in outgoing.decode('ascii').splitlines():
            raw = subprocess.check_output(['git', 'cat-file', 'commit', oid],
                                          stderr=subprocess.PIPE, env=git_env)
            raw.decode('utf-8')
            if b'\x00' in raw:
                raise ValueError('binary commit metadata unsupported')
            raw_objects.append({'oid': oid, 'sha256': digest(raw)})
            payloads.append(raw)
        if any(line == b'GIT binary patch' or
               (line.startswith(b'Binary files ') and line.endswith(b' differ'))
               for line in data.splitlines()):
            raise ValueError('binary Git patch unsupported; publication blocked')
        if not data:
            raise ValueError('empty Git range')
        data.decode('utf-8')
        revision = {'range': args.git_range, 'resolved_range': resolved_range, 'sha256': digest(data),
                    'raw_commits': raw_objects}
        payloads.append(data)
    command = args.command
    if command and command[0] == '--':
        command = command[1:]
    if not command or not payloads:
        raise ValueError('explicit command and publication inputs required')
    payloads.append(json.dumps(command, ensure_ascii=False).encode())
    return {'files': files, 'git': revision, 'command_sha256': digest(payloads[-1]),
            'cwd': str(Path.cwd().resolve()), 'target': args.target}, payloads, command


def state_dir():
    root = Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state')))
    if not root.is_absolute():
        raise ValueError('absolute state home required')
    root = root.resolve()
    # Receipts must not be published with any enclosing Git worktree.
    ancestor = root
    while not ancestor.exists():
        ancestor = ancestor.parent
    probe = subprocess.run(['git', '-C', str(ancestor), 'rev-parse', '--show-toplevel'],
                           capture_output=True)
    if probe.returncode == 0:
        raise ValueError('state home must be outside Git worktrees')
    dest = root/'ai-agent-workflow-lite'/'publication-check'
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    return dest.resolve()


def scan(payloads):
    env = {k: v for k, v in os.environ.items()
           if k not in ('GITLEAKS_CONFIG', 'GITLEAKS_CONFIG_TOML')}
    # Use a clean cwd and built-in rules; neither repo config nor allow comments
    # may suppress a finding. Scanner output is never printed or persisted.
    with tempfile.TemporaryDirectory(prefix='publication-scan-') as tmp:
        for payload in payloads:
            result = subprocess.run(['gitleaks', 'stdin', '--no-banner', '--redact=100',
                '--ignore-gitleaks-allow', '--gitleaks-ignore-path', tmp+'/no-ignore',
                '--timeout', '30'], input=payload, capture_output=True, cwd=tmp, env=env,
                timeout=35)
            if result.returncode != 0:
                raise ValueError('secret scan detected a candidate or failed; publication blocked')


def check_policy(root):
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError('canonical policy root required')
    common = Path(subprocess.check_output(['git', '-C', str(root), 'rev-parse',
        '--path-format=absolute', '--git-common-dir'], stderr=subprocess.PIPE,
        timeout=10).decode().strip())
    guard = common/'hooks/workflow-policy-guard.py'
    if guard.is_symlink() or not guard.is_file():
        raise ValueError('approved installed guard required')
    identity = subprocess.check_output([sys.executable, str(guard), 'identity'], cwd=root,
        stderr=subprocess.PIPE, timeout=10)
    return {'root': str(root), 'common': str(common), 'guard': digest(guard.read_bytes()),
            'policy': json.loads(identity)}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'publish'))
    parser.add_argument('--policy-root', required=True)
    parser.add_argument('--target', required=True, help='exact remote/repository/object')
    parser.add_argument('--file', action='append', default=[])
    parser.add_argument('--git-range', help='all outgoing commits, e.g. origin/main..HEAD')
    parser.add_argument('--context-reviewed', action='store_true',
                        help='Root attests contextual review of all inputs and command')
    parser.add_argument('--receipt')
    # Separate command explicitly, so gate options cannot be confused with CLI args.
    argv = sys.argv[1:]
    if '--' not in argv:
        parser.error('separate publication command with --')
    split = argv.index('--')
    args = parser.parse_args(argv[:split])
    args.command = argv[split+1:]
    try:
        policy = check_policy(args.policy_root)
        identity, payloads, command = snapshot(args)
        identity['policy'] = policy
        directory = state_dir()
        if args.mode == 'prepare':
            if not args.context_reviewed:
                raise ValueError('contextual review attestation required')
            scan(payloads)
            record = {'version': 1, 'identity': identity, 'context_reviewed': True,
                      'scan': 'gitleaks built-in rules passed',
                      'at': datetime.now(timezone.utc).isoformat()}
            fd, path = tempfile.mkstemp(prefix='receipt-', suffix='.json', dir=directory)
            with os.fdopen(fd, 'w') as stream:
                json.dump(record, stream, sort_keys=True)
            print(path)
        else:
            if not args.receipt:
                raise ValueError('receipt required')
            receipt = Path(args.receipt)
            if receipt.is_symlink() or receipt.resolve().parent != directory:
                raise ValueError('untrusted receipt location')
            with receipt.open('r+') as stream:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                record = json.load(stream)
                if (record.get('version') != 1 or record.get('identity') != identity or
                        record.get('context_reviewed') is not True or
                        record.get('scan') != 'gitleaks built-in rules passed' or
                        record.get('attempted')):
                    raise ValueError('missing, changed or consumed verification; publication blocked')
                # Repeat automated scan immediately before the attempt. Persist
                # consumption first: failures never silently authorize retries.
                scan(payloads)
                record['attempted'] = True
                stream.seek(0)
                json.dump(record, stream, sort_keys=True)
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
                if check_policy(args.policy_root) != policy:
                    raise ValueError('policy changed before dispatch')
                return subprocess.run(command, check=False).returncode
    except (OSError, ValueError, subprocess.SubprocessError):
        # Exceptions may contain secret command arguments or scanner output.
        print('publication-check: verification failed; publication blocked', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
