#!/usr/bin/env python3
"""Stage a trusted publisher copy outside a requester's workspace. Does not start it."""
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys


def put(path, content, mode):
    if path.is_symlink():
        raise ValueError('symlink refused')
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError('existing trusted copy differs; no overwrite')
        return
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(content); stream.flush(); os.fsync(stream.fileno())
    if path.read_bytes() != content:
        raise ValueError('read-back failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--home', required=True)
    parser.add_argument('--policy', required=True)
    args = parser.parse_args()
    try:
        workspace = Path(args.workspace).resolve(strict=True)
        home = Path(args.home)
        if not home.is_absolute():
            raise ValueError('absolute home required')
        home = home.resolve()
        if home == workspace or workspace in home.parents or home in workspace.parents:
            raise ValueError('overlapping trust domains')
        existing = home
        while not existing.exists():existing=existing.parent
        if subprocess.run(['git','-C',str(existing),'rev-parse','--show-toplevel'],capture_output=True).returncode == 0:
            raise ValueError('trusted home must be outside Git worktrees')
        home.mkdir(parents=True, mode=0o700, exist_ok=True)
        source = Path(__file__).resolve().parent
        put(home/'publisher.py', (source/'publisher.py').read_bytes(), 0o700)
        put(home/'publisher-sandbox.py', (source/'publisher-sandbox.py').read_bytes(), 0o700)
        for name in ('install-gitleaks-hook.py', 'pre-push-gitleaks.py'):
            put(home/name, (source/name).read_bytes(), 0o700)
        put(home/'policy.json', Path(args.policy).read_bytes(), 0o600)
        key = home/'approval.key'
        if key.is_symlink():raise ValueError('key symlink refused')
        if not key.exists():put(key,secrets.token_bytes(32),0o600)
        if len(key.read_bytes()) != 32:raise ValueError('invalid existing key')
        print('Trusted copy staged; service not started and current Root not migrated:', home)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print('install-publisher: blocked; inspect trusted paths before retrying',file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
