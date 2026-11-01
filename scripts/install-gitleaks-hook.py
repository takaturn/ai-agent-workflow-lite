#!/usr/bin/env python3
"""Install without overwriting existing hooks or changing Git configuration."""
from pathlib import Path
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile


def approved_previous_hook(common, installed):
    state = json.loads((common/'workflow-policy-approvals.json').read_bytes())
    env = dict(os.environ, GIT_NO_REPLACE_OBJECTS='1', GIT_GRAFT_FILE=os.devnull)
    for approval in state['approvals'][:-1]:
        revision = approval['revision']
        if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', revision):
            raise ValueError('invalid approval revision')
        prior = subprocess.check_output(['git', 'show',
            revision+':scripts/pre-push-gitleaks.py'], stderr=subprocess.PIPE,
            timeout=10, env=env)
        if installed == prior:
            return True
    return False


def approved_current_hook(common, source):
    """An upgrade installs only the bytes of this repository's latest approval."""
    state = json.loads((common/'workflow-policy-approvals.json').read_bytes())
    revision = state['approvals'][-1]['revision']
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', revision):
        raise ValueError('invalid approval revision')
    env = dict(os.environ, GIT_NO_REPLACE_OBJECTS='1', GIT_GRAFT_FILE=os.devnull)
    approved = subprocess.check_output(['git', 'show', revision+':scripts/pre-push-gitleaks.py'],
                                       stderr=subprocess.PIPE, timeout=10, env=env)
    return source == approved


def main():
    try:
        if len(sys.argv) > 2 or (len(sys.argv) == 2 and sys.argv[1] != '--upgrade-approved'):
            raise ValueError('unsupported installer option')
        upgrade = len(sys.argv) == 2
        configured = subprocess.run(['git', 'config', '--get', 'core.hooksPath'],
                                    capture_output=True)
        if configured.returncode != 1:
            raise ValueError('existing hook configuration or Git error')
        common = Path(subprocess.check_output(['git', 'rev-parse', '--git-common-dir'],
                      stderr=subprocess.PIPE).decode().strip())
        if any(parent.is_symlink() for parent in (common, *common.parents)):
            raise ValueError('common Git directory symlink')
        if upgrade:
            lock_path = common/'.workflow-policy.lock'
            if lock_path.is_symlink():
                raise ValueError('policy lock symlink')
            lock = lock_path.open('a')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = common/'workflow-policy-approvals.json'
        if Path('config/publication-policy.json').exists() and not state.exists():
            chosen = subprocess.run(['git', 'config', '--get', 'workflow.policyRemote'],
                                    capture_output=True, text=True)
            if chosen.returncode not in (0, 1):
                raise ValueError('invalid policy remote configuration')
            remote = chosen.stdout.strip() if chosen.returncode == 0 else 'origin'
            subprocess.run([sys.executable, 'scripts/policy-guard.py', 'bootstrap',
                            '--remote-name', remote], check=True,
                           capture_output=True, timeout=60)
        if state.exists() or Path('config/publication-policy.json').exists():
            subprocess.run([sys.executable, str(common/'hooks/workflow-policy-guard.py'), 'check'],
                           check=True, capture_output=True, timeout=10)
        hook = Path(subprocess.check_output(['git', 'rev-parse', '--git-path',
                    'hooks/pre-push'], stderr=subprocess.PIPE).decode().strip()).absolute()
        if any(parent.is_symlink() for parent in (hook.parent, *hook.parent.parents)):
            raise ValueError('hook parent symlink')
        source = Path(__file__).resolve().with_name('pre-push-gitleaks.py').read_bytes()
        if upgrade and not approved_current_hook(common, source):
            raise ValueError('replacement hook is not the approved version')
        if hook.is_symlink():
            raise ValueError('existing symlink')
        if hook.exists():
            if not hook.is_file() or not os.access(hook, os.X_OK):
                raise ValueError('existing hook is not executable regular file')
            installed = hook.read_bytes()
            before = hook.stat(follow_symlinks=False)
            if installed == source:
                print('pre-push: identical executable hook already installed')
                return 0
            if not upgrade or not approved_previous_hook(common, installed):
                raise ValueError('existing hook is not a prior approved version')
        hook.parent.mkdir(parents=True, exist_ok=True)
        # The normal path creates exclusively. A requested, approved upgrade
        # atomically replaces only a verified older copy, with no absent gap.
        fd, staged = tempfile.mkstemp(prefix='.pre-push-', dir=hook.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(source)
                stream.flush()
                os.fchmod(stream.fileno(), 0o700)
                os.fsync(stream.fileno())
            if upgrade and hook.exists():
                after = hook.stat(follow_symlinks=False)
                identity = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size,
                                         stat.st_mtime_ns, stat.st_mode)
                if (hook.is_symlink() or identity(after) != identity(before) or
                        hook.read_bytes() != installed):
                    raise ValueError('hook changed during upgrade')
                os.replace(staged, hook)
                staged = None
            else:
                # Hard-link creation is atomic and refuses an existing target.
                os.link(staged, hook)
        finally:
            if staged is not None:
                os.unlink(staged)
        if hook.read_bytes() != source or not os.access(hook, os.X_OK):
            raise ValueError('hook read-back failed')
        print('Installed and verified native pre-push hook:', hook)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print('hook install blocked: existing configuration/hook or installation error; inspect before retrying', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
