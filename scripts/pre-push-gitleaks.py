#!/usr/bin/env python3
"""Native Git pre-push hook; copy to the common Git hooks/pre-push path."""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


class UnsupportedSubmodule(ValueError):
    pass


def git(*args):
    env = dict(os.environ, GIT_NO_REPLACE_OBJECTS='1', GIT_GRAFT_FILE=os.devnull)
    return subprocess.check_output(['git', '-c', 'color.ui=false', *args],
                                   stderr=subprocess.PIPE, timeout=60, env=env)


def commit(oid):
    return git('rev-parse', '--verify', '--end-of-options', oid+'^{commit}').decode().strip()


def snapshots(lines):
    payloads = []
    count = 0
    for line in lines:
        fields = line.split()
        if len(fields) != 4:
            raise ValueError('invalid Git hook input')
        local_ref, local_oid, remote_ref, remote_oid = fields
        if (len(local_oid) not in (40, 64) or len(remote_oid) != len(local_oid) or
                not re.fullmatch('[0-9a-f]+', local_oid) or
                not re.fullmatch('[0-9a-f]+', remote_oid)):
            raise ValueError('invalid Git object identity')
        if set(local_oid) == {'0'}:
            continue  # Deletion sends no new content.
        count += 1
        # Include annotated tag messages, also for nested tags. Non-commit
        # destinations (raw tree/blob tags) are unsupported and fail closed.
        current = local_oid
        while git('cat-file', '-t', current).strip() == b'tag':
            tag = git('cat-file', 'tag', current)
            payloads.append(tag)
            first = tag.splitlines()[0].split()
            if len(first) != 2 or first[0] != b'object':
                raise ValueError('invalid tag')
            current = first[1].decode('ascii')
            if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', current):
                raise ValueError('invalid tag target')
        head = commit(local_oid)
        base = None
        if set(remote_oid) != {'0'}:
            try:
                base = commit(remote_oid)
            except subprocess.CalledProcessError:
                # Without the advertised old object locally, inspect all
                # history reachable from the new object rather than omit it.
                pass
        revision = base+'..'+head if base else head
        # Formatted log omits extra headers and embedded mergetags. Inspect
        # the original commit objects as well as the file patches.
        for outgoing in git('rev-list', revision, '--').decode('ascii').splitlines():
            tree = git('ls-tree', '-r', '-z', '--full-tree', outgoing)
            if any(entry.startswith(b'160000 ') for entry in tree.split(b'\x00')):
                raise UnsupportedSubmodule()
            payloads.append(git('cat-file', 'commit', outgoing))
        patch = git('-c', 'core.quotePath=true', 'log', '--no-ext-diff',
                    '--no-textconv', '--no-notes', '--format=fuller', '--root',
                    '--no-renames', '--binary', '-p', '--diff-merges=first-parent',
                    revision, '--')
        if any(row == b'GIT binary patch' or
               (row.startswith(b'Binary files ') and row.endswith(b' differ'))
               for row in patch.splitlines()):
            raise ValueError('binary patch unsupported')
        payloads.append(patch)
    return payloads, count


def main():
    try:
        # Git supplies remote name and location as argv; never print them.
        if len(sys.argv) != 3:
            raise ValueError('not a Git pre-push invocation')
        lines = sys.stdin.read().splitlines()
        common = Path(git('rev-parse', '--git-common-dir').decode().strip())
        state = common/'workflow-policy-approvals.json'
        if state.exists() or Path('config/publication-policy.json').exists():
            guard = common/'hooks/workflow-policy-guard.py'
            result = subprocess.run([sys.executable, str(guard), 'pre-push',
                                     '--remote-name', sys.argv[1],
                                     '--remote-location', sys.argv[2]],
                input='\n'.join(lines)+'\n', text=True, capture_output=True, timeout=60)
            if result.returncode:
                print('pre-push: unapproved policy change or policy guard failure; push blocked', file=sys.stderr)
                return 2
        index = git('ls-files', '--stage', '-z')
        if any(entry.startswith(b'160000 ') for entry in index.split(b'\x00')):
            raise UnsupportedSubmodule()
        if lines and git('rev-parse', '--is-shallow-repository').strip() != b'false':
            raise ValueError('shallow history unsupported')
        payloads, count = snapshots(lines)
        if not count:
            return 0
        env = {k: v for k, v in os.environ.items()
               if k not in ('GITLEAKS_CONFIG', 'GITLEAKS_CONFIG_TOML')}
        with tempfile.TemporaryDirectory(prefix='pre-push-gitleaks-') as tmp:
            # Explicitly extend built-in rules: repository/env configuration
            # and ignore files cannot silently suppress findings.
            config = Path(tmp)/'rules.toml'
            config.write_text('[extend]\nuseDefault = true\n')
            for payload in payloads:
                payload.decode('utf-8')
                if b'\x00' in payload:
                    raise ValueError('binary input unsupported')
                result = subprocess.run(['gitleaks', 'stdin', '--config', str(config),
                    '--ignore-gitleaks-allow', '--gitleaks-ignore-path', tmp+'/no-ignore',
                    '--no-banner', '--redact=100', '--timeout', '60'], input=payload,
                    capture_output=True, cwd=tmp, env=env, timeout=65)
                if result.returncode != 0:
                    raise ValueError('candidate or scanner failure')
        print('pre-push: Gitleaks passed for all outgoing ref updates', file=sys.stderr)
        return 0
    except UnsupportedSubmodule:
        print('pre-push: submodule content is unsupported; push blocked', file=sys.stderr)
        return 2
    except (OSError, ValueError, subprocess.SubprocessError):
        # Neither candidate values nor raw diagnostics/URLs reach the terminal.
        print('pre-push: secret scan detected a candidate or failed; push blocked', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
