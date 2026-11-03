#!/usr/bin/env python3
"""Execute a requester without host home/network/proc, exposing only publisher IPC.
This launches a new process, not the current Root agent session (Claude Code or Codex). Trusted operators must
launch all requesters through the protected copy; host processes remain privileged.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--socket', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        workspace = Path(args.workspace).resolve(strict=True)
        endpoint = Path(args.socket).resolve(strict=True)
        protected = endpoint.parent
        if (not workspace.is_dir() or not endpoint.is_socket() or workspace == protected or
                workspace in protected.parents or protected in workspace.parents):
            raise ValueError('unsafe mounts')
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        if not command:
            raise ValueError('command required')
        client = Path(__file__).resolve().with_name('publisher.py')
        if workspace == Path('/'):
            raise ValueError('root filesystem mount forbidden')
        result = subprocess.run(['bwrap', '--unshare-all', '--die-with-parent', '--new-session',
            '--clearenv', '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin',
            '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
            '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/home/agent',
            '--dir', '/run', '--setenv', 'HOME', '/home/agent', '--setenv', 'PATH', '/usr/bin:/bin',
            '--setenv', 'GIT_CONFIG_NOSYSTEM', '1', '--setenv', 'GIT_CONFIG_GLOBAL', '/dev/null',
            '--bind', str(workspace), '/workspace', '--chdir', '/workspace',
            '--ro-bind', str(endpoint), '/run/publisher.sock', '--ro-bind', str(client), '/publisher.py',
            '--cap-drop', 'ALL', '--', *command], close_fds=True)
        return result.returncode
    except (OSError, ValueError, subprocess.SubprocessError):
        print('publisher-sandbox: launch refused', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
