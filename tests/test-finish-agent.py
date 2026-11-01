#!/usr/bin/env python3
"""Root acceptance/cleanup gates against mocked Herdr, never real panes."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile

BASE = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    binaries = root / 'bin'
    binaries.mkdir()
    state = root / 'run.fixture'
    state.mkdir()
    summary = state / 'summary.json'
    fake = binaries / 'herdr'
    fake.write_text('''#!/usr/bin/env python3
import json,os,sys,pathlib
root=pathlib.Path(os.environ['TEST_DIR']); args=sys.argv[1:]
with (root/'actions').open('a') as log: log.write(' '.join(args)+'\\n')
closed=(root/'closed').exists()
if args[:2]==['pane','layout']:
 panes=[{'pane_id':'parent'},{'pane_id':'different'},{'pane_id':'unrelated'}]
 if not closed or os.environ.get('STALE_LAYOUT')=='1':panes.append({'pane_id':'child'})
 result={'layout':{'panes':panes}}
elif args[:2]==['pane','get']:
 result={'pane':{'pane_id':args[2],'cwd':str(root),'agent_status':os.environ.get('TEST_STATUS','done')}}
elif args[:2]==['agent','get']:
 if closed:sys.exit(1)
 result={'agent':{'pane_id':'child','cwd':str(root),'name':os.environ.get('TEST_NAME','review-fixture'),'agent_status':os.environ.get('TEST_STATUS','done')}}
elif args[:2]==['pane','close']:
 assert args[2]=='child'
 if os.environ.get('CLOSE_FAIL')=='1':sys.exit(1)
 (root/'closed').touch()
 if os.environ.get('LOSE_CLOSE_REPLY')=='1':sys.exit(1)
 result={'type':'ok'}
else:sys.exit(99)
print(json.dumps({'result':result}))
''')
    fake.chmod(0o755)
    real_jq = shutil.which('jq')
    assert real_jq is not None
    jq = binaries / 'jq'
    jq.write_text(f'''#!{sys.executable}
import os,sys
if os.environ.get('FAIL_ACCEPT_WRITE')=='1' and any('.root_acceptance="accepted"' in argument for argument in sys.argv[1:]):sys.exit(91)
if os.environ.get('FAIL_CLOSE_RECORD')=='1' and 'closed' in sys.argv[1:] and any('.root_acceptance="accepted"' in argument for argument in sys.argv[1:]):sys.exit(92)
os.execv({real_jq!r}, [{real_jq!r}, *sys.argv[1:]])
''')
    jq.chmod(0o755)
    original = dict(run_id='run.fixture', parent_pane_id='parent', pane_id='child', agent_name='review-fixture', worktree=str(root), status='returned', exit_code=0, phase='result_collected', root_acceptance='pending', cleanup='retained')
    env = dict(os.environ, TEST_DIR=str(root), HERDR_ENV='1', HERDR_PANE_ID='parent', PATH=str(binaries)+os.pathsep+os.environ['PATH'])
    def reset(**changes):
        summary.write_text(json.dumps(dict(original, **changes)))
        (root / 'actions').write_text('')
        (root / 'closed').unlink(missing_ok=True)
    def run(**changes):
        return subprocess.run(['bash', str(BASE / '.agents/scripts/finish-agent.sh'), str(summary), 'Root verified artifact and focused checks'], env=dict(env, **changes), capture_output=True, text=True, timeout=10)
    reset()
    process = run()
    assert process.returncode == 0, process
    data = json.loads(summary.read_text())
    assert data['root_acceptance'] == 'accepted' and data['cleanup'] == 'closed', data
    assert data['accepted_by'] == 'parent' and data['acceptance_note'].startswith('Root verified'), data
    actions = (root / 'actions').read_text()
    assert actions.count('pane close child') == 1 and 'pane close parent' not in actions and 'pane close unrelated' not in actions, actions
    assert run().returncode == 0
    assert (root / 'actions').read_text().count('pane close child') == 1
    for changes, fields, reason in [({'TEST_STATUS':'working'}, {}, 'not settled'), ({'TEST_STATUS':'blocked'}, {}, 'not settled'), ({'TEST_STATUS':'unknown'}, {}, 'not settled'), ({'TEST_NAME':'other-agent'}, {}, 'identity mismatch'), ({'HERDR_PANE_ID':'different'}, {}, 'parent mismatch'), ({}, {'worktree':'/different-worktree'}, 'identity mismatch'), ({}, {'status':'failed'}, 'not a returned result'), ({}, {'exit_code':None}, 'not a returned result'), ({}, {'pane_id':'parent'}, 'child equals parent'), ({'FAIL_ACCEPT_WRITE':'1'}, {}, 'cannot record acceptance')]:
        reset(**fields)
        process = run(**changes)
        assert process.returncode != 0 and reason in process.stderr, (reason, process)
        assert 'pane close' not in (root / 'actions').read_text(), process
        assert json.loads(summary.read_text())['root_acceptance'] == 'pending'
    reset()
    process = run(CLOSE_FAIL='1')
    assert process.returncode != 0 and 'cleanup unverified' in process.stderr, process
    data = json.loads(summary.read_text())
    assert data['root_acceptance']=='accepted' and data['cleanup']=='retained', data
    process = run()
    assert process.returncode != 0 and 'close already attempted' in process.stderr, process
    assert (root / 'actions').read_text().count('pane close child') == 1
    reset()
    process = run(LOSE_CLOSE_REPLY='1')
    assert process.returncode == 0 and json.loads(summary.read_text())['cleanup']=='closed', process
    reset()
    process = run(FAIL_CLOSE_RECORD='1')
    assert process.returncode != 0 and (root / 'closed').exists(), process
    assert json.loads(summary.read_text())['cleanup']=='retained'
    process = run()
    assert process.returncode == 0 and json.loads(summary.read_text())['cleanup']=='closed', process
    assert (root / 'actions').read_text().count('pane close child') == 1
    reset()
    process = run(LOSE_CLOSE_REPLY='1', STALE_LAYOUT='1')
    assert process.returncode != 0 and (root / 'closed').exists(), process
    process = run(STALE_LAYOUT='1')
    assert process.returncode != 0 and 'close already attempted' in process.stderr, process
    assert (root / 'actions').read_text().count('pane close child') == 1
    assert run().returncode == 0 and json.loads(summary.read_text())['cleanup']=='closed'
    def run_failed(**changes):
        return subprocess.run(['bash', str(BASE / '.agents/scripts/finish-agent.sh'), '--failed', str(summary), 'Root diagnosed: start failed, nothing usable'], env=dict(env, **changes), capture_output=True, text=True, timeout=10)
    reset(status='failed', phase='pane_created', exit_code=2)
    process = run_failed()
    assert process.returncode == 0, process
    data = json.loads(summary.read_text())
    assert (data['root_acceptance'], data['cleanup'], data['accepted_by']) == ('accepted', 'closed', 'parent'), data
    assert data['failure_resolution'].startswith('Root diagnosed'), data
    assert (root / 'actions').read_text().count('pane close child') == 1
    reset(status='prelaunch_error', phase='initialized', exit_code=2, pane_id='')
    process = run_failed()
    assert process.returncode == 0 and 'pane close' not in (root / 'actions').read_text(), process
    assert json.loads(summary.read_text())['cleanup'] == 'closed'
    for changes, fields, reason in [({}, {}, 'not a failed run'), ({'HERDR_PANE_ID':'different'}, {'status':'failed'}, 'parent mismatch')]:
        reset(**fields)
        process = run_failed(**changes)
        assert process.returncode != 0 and reason in process.stderr, (reason, process)
        assert json.loads(summary.read_text())['root_acceptance'] == 'pending'
    print('PASS: explicit Root acceptance, verified cleanup, idempotence, identity/state refusal, close failure and lost reply')
