#!/usr/bin/env python3
"""Write per-step durations of this workflow run to $GITHUB_STEP_SUMMARY.

Reads the run's jobs JSON (from `gh api`, or a file given as argv[1]) and
flags the review triggers of tests/README.md. It never fails the workflow.
"""
from datetime import datetime
import json
import os
import re
import subprocess
import sys

TOTAL_LIMIT = 70  # seconds, tests/README.md (provisional)
STEP_LIMIT = 15
# Network-dependent preparation is excluded from the total.
PREPARATION = re.compile(r'^(Set up job|Complete job|Run actions/|Post |Install |Step times)')


def seconds(step):
    parse = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00'))
    return (parse(step['completed_at']) - parse(step['started_at'])).total_seconds()


def report(jobs):
    lines = ['## Step times', '', '| job | step | s | |', '|---|---|---:|---|']
    total = 0.0
    over = []
    for job in jobs:
        for step in job.get('steps', []):
            if not step.get('started_at') or not step.get('completed_at'):
                continue
            value = seconds(step)
            preparation = bool(PREPARATION.match(step['name']))
            mark = 'preparation' if preparation else ''
            if not preparation:
                total += value
                if value > STEP_LIMIT:
                    mark = f'> {STEP_LIMIT}s'
                    over.append(step['name'])
            lines.append(f"| {job['name']} | {step['name']} | {value:.0f} | {mark} |")
    lines += ['', f'Test steps total: {total:.0f}s (review trigger: > {TOTAL_LIMIT}s, or one step > {STEP_LIMIT}s)']
    if total > TOTAL_LIMIT or over:
        lines.append('**Review trigger reached** — see tests/README.md.')
    return '\n'.join(lines) + '\n'


def main():
    try:
        if len(sys.argv) > 1:
            data = json.loads(open(sys.argv[1]).read())
        else:
            path = (f"repos/{os.environ['GITHUB_REPOSITORY']}/actions/runs/"
                    f"{os.environ['GITHUB_RUN_ID']}/attempts/{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}/jobs")
            data = json.loads(subprocess.run(['gh', 'api', path], check=True,
                                             capture_output=True, text=True).stdout)
        text = report(data['jobs'])
    except Exception as error:  # Timing is informational only.
        text = f'## Step times\n\nunavailable: {type(error).__name__}\n'
    target = os.environ.get('GITHUB_STEP_SUMMARY')
    if target:
        with open(target, 'a') as summary:
            summary.write(text)
    print(text, end='')


if __name__ == '__main__':
    main()
