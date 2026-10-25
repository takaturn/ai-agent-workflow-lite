#!/usr/bin/env python3
"""Real namespace/IPC and local bare receiver tests. GitHub API transport is a stub."""
import base64
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

BASE = Path(__file__).resolve().parents[1]
PUBLISHER = BASE/'scripts/publisher.py'
SANDBOX = BASE/'scripts/publisher-sandbox.py'


class PublisherTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(shutil.which('bwrap'), 'bubblewrap is required; boundary tests must not skip')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root/'workspace'; self.work.mkdir()
        self.private = self.root/'trusted'; self.private.mkdir()
        self.bin = self.private/'bin'; self.bin.mkdir()
        self.receiver = self.private/'receiver.git'
        self.output = self.private/'api.json'
        subprocess.run(['git','init','-q','--bare',str(self.receiver)],check=True,capture_output=True)
        scanner = self.bin/'gitleaks'
        scanner.write_text('#!/usr/bin/python3\nimport os,sys\ndata=sys.stdin.buffer.read()\nsys.exit(int(os.environ.get("SCANNER_FAILURE","0")) or (2 if b"fixture-scanner-error" in data else (1 if b"fixture-sensitive" in data else 0)))\n')
        scanner.chmod(0o700)
        if os.environ.get('PUBLISHER_TEST_REAL_SCANNER') == '1':
            shutil.copy2(shutil.which('gitleaks'), scanner)
        gh = self.bin/'gh'
        gh.write_text('#!/usr/bin/python3\nimport json,sys\nfrom pathlib import Path\np=Path('+repr(str(self.output))+')\nif "POST" in sys.argv:\n d=json.load(sys.stdin); p.write_text(json.dumps({"body":d["body"],"id":1}))\nprint(p.read_text())\n')
        gh.chmod(0o700)
        self.policy = self.private/'policy.json'
        self.policy.write_text(json.dumps({'target':'fixture/repository','subject':'fixture-root',
            'git_remote':self.receiver.as_uri(),'git_ref':'refs/heads/main',
            'api':{'comment':{'method':'POST','path':'repos/fixture/repository/issues/19/comments','fields':['body']}}}))
        self.fingerprint = hashlib.sha256(self.policy.read_bytes()).hexdigest()
        self.key = self.private/'approval.key'; self.key.write_bytes(secrets.token_bytes(32))
        self.endpoint = self.private/'publisher.sock'
        self.env = dict(os.environ, PATH=str(self.bin)+os.pathsep+os.environ['PATH'])
        self.server = subprocess.Popen([sys.executable,str(PUBLISHER),'serve','--policy',str(self.policy),
            '--key',str(self.key),'--state',str(self.private/'state'),'--socket',str(self.endpoint),
            '--workspace',str(self.work)],
            env=self.env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.addCleanup(self.stop)
        for _ in range(100):
            if self.endpoint.exists():break
            if self.server.poll() is not None:self.fail('publisher failed to start')
            time.sleep(.02)
        self.assertTrue(self.endpoint.exists())

    def stop(self):
        if self.server.poll() is None:
            self.server.terminate(); self.server.wait(timeout=5)

    def request(self, operation='comment', payload=None):
        return {'version':1,'operation':operation,'target':'fixture/repository','subject':'fixture-root',
            'nonce':secrets.token_hex(16),'expires':int(time.time())+240,'policy':self.fingerprint,
            'payload':payload or {'body':'public fixture'}}

    def approve(self, request):
        path=self.work/(request['nonce']+'.request.json'); path.write_text(json.dumps(request))
        envelope=self.work/(request['nonce']+'.envelope.json')
        result=subprocess.run([sys.executable,str(PUBLISHER),'approve','--policy',str(self.policy),
            '--key',str(self.key),'--request',str(path),'--out',str(envelope),'--context-reviewed'],
            capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        return envelope

    def inside(self, command):
        return subprocess.run([sys.executable,str(SANDBOX),'--workspace',str(self.work),
            '--socket',str(self.endpoint),'--',*command],capture_output=True,text=True,timeout=100)

    def submit(self, envelope):
        return self.inside(['/usr/bin/python3','/publisher.py','submit','--socket','/run/publisher.sock',
            '--envelope','/workspace/'+envelope.name])

    def test_approved_comment_and_no_direct_host_access(self):
        envelope=self.approve(self.request())
        result=self.submit(envelope)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(self.output.read_text())['body'],'public fixture')
        code='''from pathlib import Path
import os,socket
for path in %r:
 try:Path(path).read_bytes();raise SystemExit(5)
 except (FileNotFoundError,PermissionError):pass
assert not any(k in os.environ for k in ('GH_TOKEN','GITHUB_TOKEN','SSH_AUTH_SOCK'))
try:Path(%r).write_text('bypass');raise SystemExit(6)
except (FileNotFoundError,PermissionError):pass
try:socket.create_connection(('127.0.0.1',1),timeout=1);raise SystemExit(7)
except OSError:pass
'''%([str(self.key),str(self.policy),'/proc/'+str(self.server.pid)+'/environ'],str(self.output))
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen(1)
            port = listener.getsockname()[1]
            probe = 'import socket; s=socket.socket(); s.settimeout(1); result=s.connect_ex((\"127.0.0.1\",'+str(port)+')); assert result != 0'
            with socket.create_connection(('127.0.0.1', port), timeout=1):pass
            self.assertEqual(self.inside(['/usr/bin/python3', '-c', probe]).returncode, 0)
        self.assertEqual(self.inside(['/usr/bin/python3','-c',code]).returncode,0)
        self.assertEqual(json.loads(self.output.read_text())['body'],'public fixture')

    def test_tamper_forgery_and_replay_are_denied(self):
        request=self.request(); envelope=self.approve(request)
        original=envelope.read_bytes(); data=json.loads(original)
        data['request']['payload']['body']='changed unreviewed'; envelope.write_text(json.dumps(data))
        self.assertNotEqual(self.submit(envelope).returncode,0); self.assertFalse(self.output.exists())
        data=json.loads(original); data['approval']='0'*64; envelope.write_text(json.dumps(data))
        self.assertNotEqual(self.submit(envelope).returncode,0); self.assertFalse(self.output.exists())
        envelope.write_bytes(original)
        self.assertEqual(self.submit(envelope).returncode,0)
        self.assertNotEqual(self.submit(envelope).returncode,0)

    def test_scanner_detection_and_unknown_fields_block(self):
        bad=self.approve(self.request(payload={'body':'fixture-sensitive'}))
        result=self.submit(bad)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('fixture-sensitive',result.stdout+result.stderr)
        self.assertFalse(self.output.exists())
        request=self.request(); envelope=self.approve(request)
        data=json.loads(envelope.read_text()); data['request']['payload']['title']='unapproved'
        envelope.write_text(json.dumps(data))
        self.assertNotEqual(self.submit(envelope).returncode,0)
        self.assertFalse(self.output.exists())

    def git_request(self, content='public fixture'):
        repo=self.work/'source';repo.mkdir()
        def git(*args):
            return subprocess.check_output(['git','-c','user.name=Fixture','-c',
                'user.email=fixture@example.invalid',*args],cwd=repo,stderr=subprocess.PIPE)
        git('init','-q','-b','main');(repo/'data').write_text(content)
        git('add','data');git('commit','-qm','fixture')
        oid=git('rev-parse','HEAD').decode().strip()
        bundle=self.work/'candidate.bundle';git('bundle','create',str(bundle),'refs/heads/main')
        return self.request('git_push',{'oid':oid,'expected_oid':'0'*len(oid),
            'bundle':base64.b64encode(bundle.read_bytes()).decode()})

    def test_git_push_only_after_scan_and_old_ref_match(self):
        request=self.git_request(); envelope=self.approve(request)
        result=self.submit(envelope)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        actual=subprocess.check_output(['git','--git-dir',str(self.receiver),'rev-parse','refs/heads/main'],text=True).strip()
        self.assertEqual(actual,request['payload']['oid'])
        request['nonce']=secrets.token_hex(16); retry=self.approve(request)
        self.assertNotEqual(self.submit(retry).returncode,0)

    def test_git_secret_rejected_without_receiver_update(self):
        envelope=self.approve(self.git_request('fixture-sensitive'))
        self.assertNotEqual(self.submit(envelope).returncode,0)
        result=subprocess.run(['git','--git-dir',str(self.receiver),'show-ref'],capture_output=True)
        self.assertNotEqual(result.returncode,0)

    def test_parallel_same_approval_publishes_once(self):
        envelope=self.approve(self.request())
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(lambda _:self.submit(envelope),range(2)))
        self.assertEqual(sum(r.returncode==0 for r in outcomes),1)

    def test_scanner_failure_does_not_publish_or_refund_nonce(self):
        envelope=self.approve(self.request(payload={'body':'fixture-scanner-error'}))
        self.assertNotEqual(self.submit(envelope).returncode,0)
        self.assertFalse(self.output.exists())
        self.assertNotEqual(self.submit(envelope).returncode,0)

    def test_scope_expiry_and_unknown_operations_are_denied(self):
        for field,value in [('target','other/repository'),('subject','other-root'),
                            ('policy','0'*64),('expires',int(time.time())-1),('operation','shell')]:
            envelope=self.approve(self.request())
            data=json.loads(envelope.read_text());data['request'][field]=value
            envelope.write_text(json.dumps(data))
            self.assertNotEqual(self.submit(envelope).returncode,0)
            self.assertFalse(self.output.exists())

    def test_binary_git_content_is_rejected(self):
        envelope=self.approve(self.git_request('binary\x00content'))
        self.assertNotEqual(self.submit(envelope).returncode,0)
        self.assertNotEqual(subprocess.run(['git','--git-dir',str(self.receiver),'show-ref'],capture_output=True).returncode,0)

    def test_import_does_not_execute_source_hooks_or_config(self):
        request=self.git_request()
        source=self.work/'source'
        marker=self.private/'hook-ran'
        hook=source/'.git/hooks/post-checkout'
        hook.write_text('#!/bin/sh\nprintf unsafe > '+str(marker)+'\n');hook.chmod(0o700)
        subprocess.run(['git','-C',str(source),'config','core.hooksPath',str(hook.parent)],check=True)
        envelope=self.approve(request)
        self.assertEqual(self.submit(envelope).returncode,0)
        self.assertFalse(marker.exists())

    def test_requester_cannot_mint_approval(self):
        request=self.request()
        path=self.work/'unapproved.json';path.write_text(json.dumps(request))
        result=self.inside(['/usr/bin/python3','/publisher.py','approve','--policy',str(self.policy),
            '--key',str(self.key),'--request','/workspace/unapproved.json','--out','/workspace/forged.json','--context-reviewed'])
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.work/'forged.json').exists())

    def test_same_state_restart_preserves_replay_denial(self):
        envelope=self.approve(self.request())
        self.assertEqual(self.submit(envelope).returncode,0)
        self.stop();self.endpoint.unlink()
        self.server=subprocess.Popen([sys.executable,str(PUBLISHER),'serve','--policy',str(self.policy),
            '--key',str(self.key),'--state',str(self.private/'state'),'--socket',str(self.endpoint),
            '--workspace',str(self.work)],
            env=self.env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(100):
            if self.endpoint.exists():break
            time.sleep(.02)
        self.assertIsNone(self.server.poll())
        self.assertNotEqual(self.submit(envelope).returncode,0)

    def test_trusted_install_is_external_and_never_overwrites(self):
        home=self.root/'installed'
        command=[sys.executable,str(BASE/'scripts/install-publisher.py'),'--workspace',str(self.work),
            '--home',str(home),'--policy',str(self.policy)]
        self.assertEqual(subprocess.run(command,capture_output=True).returncode,0)
        key=(home/'approval.key').read_bytes()
        self.assertEqual(subprocess.run(command,capture_output=True).returncode,0)
        self.assertEqual((home/'approval.key').read_bytes(),key)
        (home/'publisher.py').write_text('different')
        self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)
        self.assertEqual((home/'publisher.py').read_text(),'different')
        unsafe=[sys.executable,str(BASE/'scripts/install-publisher.py'),'--workspace',str(self.work),
            '--home',str(self.work/'unsafe'),'--policy',str(self.policy)]
        self.assertNotEqual(subprocess.run(unsafe,capture_output=True).returncode,0)
        self.assertFalse((self.work/'unsafe').exists())

    def test_actual_git_bypass_inside_requester_is_denied(self):
        request=self.git_request(); envelope=self.approve(request)
        command=['/usr/bin/git','-C','/workspace/source','push','--no-verify',self.receiver.as_uri(),
                 'HEAD:refs/heads/main']
        result=self.inside(command)
        self.assertNotEqual(result.returncode,0)
        refs=subprocess.run(['git','--git-dir',str(self.receiver),'show-ref'],capture_output=True)
        self.assertNotEqual(refs.returncode,0)
        self.assertEqual(self.submit(envelope).returncode,0)


if __name__=='__main__':
    unittest.main()
