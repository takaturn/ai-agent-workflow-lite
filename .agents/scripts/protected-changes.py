#!/usr/bin/env python3
"""List the protected files a pull request changes. Informational: always exits 0.

Usage: protected-changes.py <base-commit> <head-commit>
The protected list is the union of the base and head policies, so a pull
request cannot hide a change by editing the list (SPEC: Issue #85)."""
import json
import os
import subprocess
import sys

POLICY = '.agents/config/publication-policy.json'


def git(*args):
    return subprocess.check_output(['git', *args], stderr=subprocess.PIPE, timeout=60)


def protected(revision):
    """The revision's list, or None when it has none or it is unreadable."""
    try:
        value = json.loads(git('show', f'{revision}:{POLICY}'))
        paths = value['protected_paths']
        if not isinstance(paths, list):
            return None
        return {p for p in paths if isinstance(p, str) and p}
    except (subprocess.CalledProcessError, ValueError, KeyError, TypeError):
        return None


def report(base, head):
    lists = [protected(base), protected(head)]
    if all(found is None for found in lists):
        raise ValueError('no readable protected path list')
    paths = set().union(*(found for found in lists if found))
    merge_base = git('merge-base', base, head).decode().strip()
    changed = [name.decode('utf-8', 'replace') for name in
               git('diff', '-z', '--name-only', '--no-renames', merge_base, head, '--').split(b'\0') if name]
    hits = sorted(name for name in changed
                  if any(name == p or name.startswith(p.rstrip('/')+'/') for p in paths))
    lines = ['## Protected files changed by this pull request', '']
    lines += [f'- `{name}`' for name in hits] if hits else ['None.']
    return '\n'.join(lines)+'\n'


def main():
    try:
        if len(sys.argv) != 3:
            raise ValueError('usage: protected-changes.py <base> <head>')
        text = report(sys.argv[1], sys.argv[2])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        text = f'## Protected files changed by this pull request\n\nNot determined: {type(error).__name__}\n'
    print(text, end='')
    try:
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary, 'a') as stream:
                stream.write(text)
    except OSError:
        print('protected-changes: could not write the job summary', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
