#!/usr/bin/env python3
"""Actual local pushes and hook protocol checks; no external receiver or model."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import unittest

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_tests', BASE/'tests/test-gitleaks-hook.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class PolicyGuardTest(unittest.TestCase):
    setUpFixture = native.HookTest.setUp
    process = native.HookTest.process
    git = native.HookTest.git
    install = native.HookTest.install
    write_commit = native.HookTest.write_commit
    remote_oid = native.HookTest.remote_oid
    push = native.HookTest.push

    def setUp(self):
        self.setUpFixture()
        self.assertEqual(self.push().returncode,0)
        (self.work/'scripts').mkdir()
        shutil.copy(BASE/'scripts/policy-guard.py', self.work/'scripts/policy-guard.py')
        (self.work/'config').mkdir()
        (self.work/'config/publication-policy.json').write_bytes((BASE/'config/publication-policy.json').read_bytes())
        (self.work/'AGENTS.md').write_text('Approved fixture rules\n')
        self.commit_all('policy')
        self.assertEqual(self.guard('approve','--approval-ref','fixture-initial').returncode,0)

    def commit_all(self, message):
        self.assertEqual(self.git('add','.').returncode,0)
        self.assertEqual(self.git('commit','-qm',message).returncode,0)

    def guard(self,*args,input=None,cwd=None):
        return self.process([sys.executable,str(self.work/'scripts/policy-guard.py'),*args],cwd=cwd or self.work,input=input)

    def hook(self, **event):
        payload={'hook_event_name':'PreToolUse','cwd':str(self.work),'tool_name':'Bash','tool_input':{'command':'git status'}}
        payload.update(event)
        return self.guard('hook',input=json.dumps(payload))

    def seed(self):
        self.assertEqual(self.push().returncode,0)

    def test_policy_change_requires_approval_before_push(self):
        self.seed();before=self.remote_oid()
        (self.work/'AGENTS.md').write_text('Changed rules\n')
        blocked=json.loads(self.hook().stdout)
        self.assertEqual(blocked['hookSpecificOutput']['permissionDecision'],'deny')
        self.commit_all('policy update')
        result=self.push()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('policy guard',result.stderr)
        self.assertEqual(self.remote_oid(),before)
        self.assertEqual(self.guard('approve','--approval-ref','fixture-update').returncode,0)
        self.assertEqual(self.hook().stdout,'')
        self.assertEqual(self.push().returncode,0)

    def test_deleted_intermediate_policy_is_not_hidden_by_restoration(self):
        self.seed();before=self.remote_oid()
        (self.work/'AGENTS.md').unlink();self.commit_all('delete')
        (self.work/'AGENTS.md').write_text('Approved fixture rules\n');self.commit_all('restore')
        self.assertNotEqual(self.push().returncode,0)
        self.assertEqual(self.remote_oid(),before)

    def test_missing_state_and_malformed_event_deny(self):
        for payload in ('{}','[]','invalid json'):
            result=self.guard('hook',input=payload)
            self.assertEqual(result.returncode,0)
            self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'],'deny')
        (self.work/'.git/workflow-policy-approvals.json').unlink()
        self.assertEqual(json.loads(self.hook().stdout)['hookSpecificOutput']['permissionDecision'],'deny')
        self.assertNotEqual(self.push().returncode,0)
        self.assertEqual(self.remote_oid(),self.base)

    def test_dirty_approval_reuse_and_symlink_refused(self):
        self.assertEqual(self.guard('approve','--approval-ref','fixture-initial').returncode,0)
        (self.work/'AGENTS.md').write_text('dirty rules')
        self.assertNotEqual(self.guard('approve','--approval-ref','dirty').returncode,0)
        self.commit_all('update')
        self.assertNotEqual(self.guard('approve','--approval-ref','fixture-initial').returncode,0)
        (self.work/'AGENTS.md').unlink();(self.work/'AGENTS.md').symlink_to(self.work/'data')
        self.assertEqual(json.loads(self.hook().stdout)['hookSpecificOutput']['permissionDecision'],'deny')

    def test_linked_worktree_shares_approvals(self):
        other=self.root/'linked'
        self.assertEqual(self.git('worktree','add','-qb','other',str(other)).returncode,0)
        self.assertEqual(self.guard('check',cwd=other).returncode,0)
        (other/'AGENTS.md').write_text('unapproved')
        self.assertNotEqual(self.guard('check',cwd=other).returncode,0)
        self.assertEqual(self.guard('check').returncode,0)


if __name__=='__main__':
    unittest.main()
