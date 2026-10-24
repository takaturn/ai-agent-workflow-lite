#!/usr/bin/env python3
"""Install without overwriting existing hooks or changing Git configuration."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile


def main():
    try:
        configured = subprocess.run(['git', 'config', '--get', 'core.hooksPath'],
                                    capture_output=True)
        if configured.returncode != 1:
            raise ValueError('existing hook configuration or Git error')
        hook = Path(subprocess.check_output(['git', 'rev-parse', '--git-path',
                    'hooks/pre-push'], stderr=subprocess.PIPE).decode().strip()).absolute()
        source = Path(__file__).resolve().with_name('pre-push-gitleaks.py').read_bytes()
        if hook.is_symlink():
            raise ValueError('existing symlink')
        if hook.exists():
            if hook.read_bytes() != source or not os.access(hook, os.X_OK):
                raise ValueError('existing hook differs')
            print('pre-push: identical executable hook already installed')
            return 0
        hook.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation protects a concurrent install. The installed copy
        # works in all linked worktrees and survives branch switches.
        fd, staged = tempfile.mkstemp(prefix='.pre-push-', dir=hook.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(source)
                stream.flush()
                os.fchmod(stream.fileno(), 0o700)
                os.fsync(stream.fileno())
            # Hard-link creation is atomic and refuses an existing destination.
            os.link(staged, hook)
        finally:
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
