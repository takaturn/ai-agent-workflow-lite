#!/usr/bin/env python3
"""Install without overwriting existing hooks or changing Git configuration.

--upgrade replaces an installed hook only with the version merged into the
remote's default branch, and only when the installed copy is a version from
that branch's history (SPEC: Issue #85)."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

HOOK_PATHS = ('.agents/scripts/pre-push-gitleaks.py', 'scripts/pre-push-gitleaks.py')


def git(*args, timeout=10):
    env = dict(os.environ, GIT_NO_REPLACE_OBJECTS='1', GIT_GRAFT_FILE=os.devnull)
    return subprocess.check_output(['git', *args], stderr=subprocess.PIPE, env=env, timeout=timeout)


def default_base(remote):
    """The fetched default branch of remote, verified against the remote itself."""
    if remote not in git('remote').decode().splitlines():
        raise ValueError('upgrade remote must be a configured remote')
    urls = git('remote', 'get-url', '--push', '--all', remote).decode().splitlines()
    if len(urls) != 1:
        raise ValueError('upgrade remote must have one push location')
    # Only the default branch counts as merged; no base override is accepted.
    if subprocess.run(['git', 'config', '--get', 'workflow.baseBranch'],
                      capture_output=True).returncode != 1:
        raise ValueError('base branch override is not accepted')
    prefix = f'refs/remotes/{remote}/'
    ref = git('symbolic-ref', '--quiet', prefix+'HEAD').decode().strip()
    if not ref.startswith(prefix) or ref == prefix+'HEAD':
        raise ValueError('invalid default branch')
    remote_ref = 'refs/heads/' + ref[len(prefix):]
    git('check-ref-format', remote_ref)
    base = git('rev-parse', '--verify', '--end-of-options', ref+'^{commit}').decode().strip()
    # The remote itself must name this branch as its HEAD, so a retargeted or
    # stale local symref cannot select an unmerged branch.
    advertised = git('ls-remote', '--quiet', '--exit-code', '--symref',
                     urls[0], 'HEAD', remote_ref, timeout=60).decode('ascii').splitlines()
    if sorted(advertised) != sorted([f'ref: {remote_ref}\tHEAD', f'{base}\tHEAD',
                                     f'{base}\t{remote_ref}']):
        raise ValueError('fetched default branch does not match remote HEAD')
    return base


def merged_versions(base):
    """Every hook version (current and pre-#36 path) in the base branch history."""
    versions = set()
    for commit in git('rev-list', '--full-history', base, '--', *HOOK_PATHS, timeout=60).decode('ascii').split():
        for name in HOOK_PATHS:
            shown = subprocess.run(['git', 'show', f'{commit}:{name}'], capture_output=True,
                timeout=10, env=dict(os.environ, GIT_NO_REPLACE_OBJECTS='1', GIT_GRAFT_FILE=os.devnull))
            if shown.returncode == 0:
                versions.add(shown.stdout)
    return versions


def main():
    try:
        args = sys.argv[1:]
        upgrade = args[:1] == ['--upgrade']
        if upgrade:
            args = args[1:]
        remote = 'origin'
        if upgrade and len(args) == 2 and args[0] == '--remote':
            remote, args = args[1], []
        if args:
            raise ValueError('unsupported installer option')
        configured = subprocess.run(['git', 'config', '--get', 'core.hooksPath'],
                                    capture_output=True)
        if configured.returncode != 1:
            raise ValueError('existing hook configuration or Git error')
        common = Path(git('rev-parse', '--git-common-dir').decode().strip())
        if any(parent.is_symlink() for parent in (common, *common.parents)):
            raise ValueError('common Git directory symlink')
        hook = Path(git('rev-parse', '--git-path', 'hooks/pre-push').decode().strip()).absolute()
        if any(parent.is_symlink() for parent in (hook.parent, *hook.parent.parents)):
            raise ValueError('hook parent symlink')
        source = Path(__file__).resolve().with_name('pre-push-gitleaks.py').read_bytes()
        if upgrade:
            base = default_base(remote)
            if source != git('show', base+':'+HOOK_PATHS[0]):
                raise ValueError('replacement hook is not the default branch version')
        if hook.is_symlink():
            raise ValueError('existing symlink')
        replacing = hook.exists()
        if replacing:
            if not hook.is_file() or not os.access(hook, os.X_OK):
                raise ValueError('existing hook is not executable regular file')
            installed = hook.read_bytes()
            before = hook.stat(follow_symlinks=False)
            if installed == source:
                print('pre-push: identical executable hook already installed')
                return 0
            if not upgrade or installed not in merged_versions(base):
                raise ValueError('existing hook is not a merged prior version')
        hook.parent.mkdir(parents=True, exist_ok=True)
        # The normal path creates exclusively. A requested upgrade atomically
        # replaces only a verified older copy, with no absent gap.
        fd, staged = tempfile.mkstemp(prefix='.pre-push-', dir=hook.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(source)
                stream.flush()
                os.fchmod(stream.fileno(), 0o700)
                os.fsync(stream.fileno())
            if replacing:
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
