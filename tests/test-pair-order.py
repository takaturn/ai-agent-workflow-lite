#!/usr/bin/env python3
"""Controlled lifecycle orders using mock child runners, not live AI agents."""
import itertools
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

BASE = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    scripts = root / '.agents/scripts'
    scripts.mkdir(parents=True)
    shutil.copy2(BASE / '.agents/scripts/run-pair.sh', scripts / 'run-pair.sh')
    for name in ('a', 'b'):
        (root / name).mkdir()
    task = root / 'task'
    task.write_text('No real agent.\n')
    (scripts / 'herdr-context.sh').write_text('#!/usr/bin/env bash\nexit 0\n')
    (scripts / 'check-worker-worktree.sh').write_text('''#!/usr/bin/env bash
if [[ ${CHECK_FAIL_B:-0} == 1 && $1 == b ]]; then
  printf 'injected B inspection failure\n' >&2; exit 91
fi
printf '%s\\t%s\\tfeature/issue%s\\t%s\\n' "$1" "$2" "$2" "$3"
''')
    (scripts / 'run-agent.sh').write_text('''#!/usr/bin/env bash
set -euo pipefail
id=${PWD##*/}
wait_file() {
  for ((n=0;n<200;n++)); do
    [[ -e $1 ]] && return 0
    sleep .01
  done
  printf 'barrier timeout: %s\n' "$1" >&2
  exit 90
}
if [[ $id != $START_FIRST ]]; then wait_file "$ORDER_DIR/$START_FIRST.started"; fi
printf 'start:%s\n' "$id" >>"$ORDER_DIR/events"
touch "$ORDER_DIR/$id.started"
if [[ ${START_FAIL_A:-0} == 1 && $id == a ]]; then
  printf 'error:a\n' >>"$ORDER_DIR/events"
  touch "$ORDER_DIR/a.ended"
  exit 31
fi
wait_file "$ORDER_DIR/a.started"
wait_file "$ORDER_DIR/b.started"
if [[ $id != $FINISH_FIRST ]]; then wait_file "$ORDER_DIR/$FINISH_FIRST.ended"; fi
printf 'result-%s\n' "$id"
printf 'summary-%s\n' "$id" >&2
rc=0
[[ $FAIL_SIDE != "$id" && $FAIL_SIDE != both ]] || { if [[ $id == a ]]; then rc=17; else rc=23; fi; }
printf 'end:%s:%s\n' "$id" "$rc" >>"$ORDER_DIR/events"
touch "$ORDER_DIR/$id.ended"
exit "$rc"
''')
    command = ['bash', str(scripts / 'run-pair.sh'), 'codex', 'a', '51', str(root / 'a'), 'light', str(task), 'b', '52', str(root / 'b'), 'light', str(task)]
    def run(start, finish, failure, **extra):
        for file in root.glob('*.started'):
            file.unlink()
        for file in root.glob('*.ended'):
            file.unlink()
        (root / 'events').write_text('')
        env = dict(os.environ, LAUNCH_TASK_ID="pair-order-fixture", ORDER_DIR=str(root), START_FIRST=start, FINISH_FIRST=finish, FAIL_SIDE=failure, **extra)
        process = subprocess.run(command, env=env, text=True, capture_output=True, timeout=10)
        return process, (root / 'events').read_text().splitlines()
    cases = list(itertools.product('ab', 'ab', ('none', 'a', 'b', 'both')))
    for start, finish, failure in cases:
        process, events = run(start, finish, failure)
        assert process.returncode == (0 if failure == 'none' else 2), (start, finish, failure, process)
        other_start = 'b' if start == 'a' else 'a'
        assert events[:2] == [f'start:{start}', f'start:{other_start}'], events
        end_events = [event for event in events if event.startswith('end:')]
        assert len(end_events) == 2 and end_events[0].split(':')[1] == finish, events
        for name, issue, error in (('a', 51, 17), ('b', 52, 23)):
            expected = error if failure in (name, 'both') else 0
            assert f'worker={name} issue={issue} runner_exit={expected}' in process.stdout, process
            assert f'[{name} issue={issue}] result-{name}' in process.stdout, process
            assert f'[{name} issue={issue}] summary-{name}' in process.stdout, process
    process, events = run('a', 'a', 'none', START_FAIL_A='1')
    assert process.returncode == 2 and 'worker=a issue=51 runner_exit=31' in process.stdout, process
    assert 'worker=b issue=52 runner_exit=0' in process.stdout and (root / 'b.ended').exists(), process
    process, events = run('a', 'a', 'none', CHECK_FAIL_B='1')
    assert process.returncode != 0 and 'injected B inspection failure' in process.stderr, process
    assert events == [], events
    print(f'PASS: {len(cases)} controlled startup/completion/error order cases, early child failure isolation, preflight inspection failure')
