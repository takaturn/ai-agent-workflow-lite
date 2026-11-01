#!/usr/bin/env python3
"""Actual local pushes and hook protocol checks; no external receiver or model."""
import importlib.util
import json
import os
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
        self.assertEqual(self.git('fetch','-q','origin','main').returncode,0)
        self.assertEqual(self.git('symbolic-ref','refs/remotes/origin/HEAD',
                                  'refs/remotes/origin/main').returncode,0)
        (self.work/'scripts').mkdir()
        shutil.copy(BASE/'scripts/policy-guard.py', self.work/'scripts/policy-guard.py')
        shutil.copy(BASE/'scripts/install-gitleaks-hook.py',
                    self.work/'scripts/install-gitleaks-hook.py')
        shutil.copy(BASE/'scripts/pre-push-gitleaks.py',
                    self.work/'scripts/pre-push-gitleaks.py')
        (self.work/'config').mkdir()
        (self.work/'config/publication-policy.json').write_bytes((BASE/'config/publication-policy.json').read_bytes())
        (self.work/'AGENTS.md').write_text('Approved fixture rules\n')
        self.commit_all('policy')
        result=self.guard('approve','--approval-ref','fixture-initial')
        self.assertEqual(result.returncode,0,result.stderr)

    def commit_all(self, message):
        self.assertEqual(self.git('add','.').returncode,0)
        self.assertEqual(self.git('commit','-qm',message).returncode,0)

    def guard(self,*args,input=None,cwd=None):
        return self.process([sys.executable,str(self.work/'scripts/policy-guard.py'),*args],cwd=cwd or self.work,input=input)

    def hook(self, **event):
        payload={'hook_event_name':'PreToolUse','cwd':str(self.work),'tool_name':'Bash','tool_input':{'command':'git status'}}
        payload.update(event)
        common=self.git('rev-parse','--path-format=absolute','--git-common-dir').stdout.strip()
        return self.guard('hook','--policy-root',str(self.work),'--expected-common-dir',common,input=json.dumps(payload),cwd=event.get('cwd',str(self.work)))

    def test_explicit_binding_from_non_git_directory(self):
        other=self.work.parent/'non-git'
        other.mkdir()
        self.assertEqual(self.hook(cwd=str(other)).stdout,'')

    def test_wrong_common_directory_is_denied(self):
        event=json.dumps({'hook_event_name':'PreToolUse','cwd':str(self.work)})
        result=self.guard('hook','--policy-root',str(self.work),'--expected-common-dir',str(self.work.parent),input=event)
        self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'],'deny')

    def test_legacy_hook_checks_current_worktree_policy(self):
        event = json.dumps({
            'hook_event_name': 'PreToolUse', 'cwd': str(self.work)})
        self.assertEqual(self.guard('hook', input=event).stdout, '')
        (self.work/'AGENTS.md').write_text('Unapproved legacy change\n')
        result = self.guard('hook', input=event)
        self.assertEqual(
            json.loads(result.stdout)['hookSpecificOutput'].get('permissionDecision'),
            None)

    def test_linked_worktree_cannot_reuse_original_binding(self):
        linked=self.work.parent/'linked'
        self.assertEqual(self.git('worktree','add','-q','-b','linked',str(linked)).returncode,0)
        (linked/'AGENTS.md').write_text('Unapproved linked change\n')
        self.assertEqual(json.loads(self.hook(cwd=str(linked)).stdout)['hookSpecificOutput']['permissionDecision'],'deny')
        common=self.git('rev-parse','--path-format=absolute','--git-common-dir').stdout.strip()
        event=json.dumps({'hook_event_name':'PreToolUse','cwd':str(linked)})
        result=self.guard('hook','--policy-root',str(linked),'--expected-common-dir',common,input=event,cwd=linked)
        self.assertIn('additionalContext',json.loads(result.stdout)['hookSpecificOutput'])
        (linked/'AGENTS.md').write_bytes((self.work/'AGENTS.md').read_bytes())
        result=self.guard('hook','--policy-root',str(linked),'--expected-common-dir',common,input=event,cwd=linked)
        self.assertEqual(result.stdout,'',result.stdout)

    def test_hook_startup_and_operation_directory_matrix(self):
        wiki=self.work.parent/'wiki-matrix'; wiki.mkdir()
        self.assertEqual(self.process(['git','init','-q',str(wiki)]).returncode,0)
        plain=self.work.parent/'plain-matrix';plain.mkdir()
        common=self.git('rev-parse','--path-format=absolute','--git-common-dir').stdout.strip()
        for changed in (False, True):
            if changed:(self.work/'AGENTS.md').write_text('Unapproved matrix change\n')
            for startup in (self.work,wiki,plain):
                for operation in (self.work,wiki,plain):
                    event=json.dumps({'hook_event_name':'PreToolUse','cwd':str(operation)})
                    result=self.guard('hook','--policy-root',str(self.work),'--expected-common-dir',common,input=event,cwd=startup)
                    with self.subTest(changed=changed,startup=startup.name,operation=operation.name):
                        if changed:self.assertIn('additionalContext',json.loads(result.stdout)['hookSpecificOutput'])
                        else:self.assertEqual(result.stdout,'',result.stdout)

    def test_linked_startup_with_external_operation_is_denied(self):
        linked=self.work.parent/'linked-startup'
        self.assertEqual(self.git('worktree','add','-q','-b','linked-startup',str(linked)).returncode,0)
        (linked/'AGENTS.md').write_text('Unapproved linked startup\n')
        outside=self.work.parent/'outside';outside.mkdir()
        common=self.git('rev-parse','--path-format=absolute','--git-common-dir').stdout.strip()
        for operation in (outside,self.work):
            result=self.guard('hook','--policy-root',str(self.work),'--expected-common-dir',common,input=json.dumps({'hook_event_name':'PreToolUse','cwd':str(operation)}),cwd=linked)
            self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'],'deny')

    def test_relative_event_directory_is_denied(self):
        self.assertEqual(json.loads(self.hook(cwd='.').stdout)['hookSpecificOutput']['permissionDecision'],'deny')

    def test_one_time_human_migration_then_ai_local_commit(self):
        source=(self.work/'scripts/policy-guard.py').read_text()
        start=source.index('            try:\n                check(value)')
        end=source.index("            if args.mode == 'identity':",start)
        legacy=source[:start]+'            check(value)\n'+source[end:]
        (self.work/'scripts/policy-guard.py').write_text(legacy)
        self.commit_all('legacy strict guard fixture')
        self.assertEqual(self.guard('approve','--approval-ref','legacy-human').returncode,0)
        installed=self.work/'.git/hooks/workflow-policy-guard.py'
        event=json.dumps({'hook_event_name':'PreToolUse','cwd':str(self.work)})
        (self.work/'scripts/policy-guard.py').write_text(source)
        (self.work/'AGENTS.md').write_text('Migration policy change\n')
        before=self.process([sys.executable,str(installed),'hook'],cwd=self.work,input=event)
        self.assertTrue(before.stdout,before.stderr)
        self.assertEqual(json.loads(before.stdout)['hookSpecificOutput']['permissionDecision'],'deny')
        self.commit_all('one-time human migration commit')
        self.assertEqual(self.guard('approve','--approval-ref','migration-human').returncode,0)
        (self.work/'AGENTS.md').write_text('Next local update\n')
        after=self.process([sys.executable,str(installed),'hook'],cwd=self.work,input=event)
        self.assertIn('additionalContext',json.loads(after.stdout)['hookSpecificOutput'])
        self.commit_all('AI local commit after migration')
        self.assertNotEqual(self.guard('check').returncode,0)

    def seed(self):
        self.assertEqual(self.push().returncode,0)

    def test_hook_keeps_policy_root_when_command_targets_wiki(self):
        wiki = self.work.parent/'wiki'
        wiki.mkdir()
        self.assertEqual(self.process(['git','init','-q',str(wiki)]).returncode,0)
        result = self.hook(cwd=str(wiki))
        self.assertEqual(result.stdout, '', result.stdout)
        # A different command directory must never hide a policy change.
        (self.work/'AGENTS.md').write_text('Unapproved change\n')
        blocked = json.loads(self.hook(cwd=str(wiki)).stdout)
        self.assertIn('additionalContext',blocked['hookSpecificOutput'])

    def test_policy_change_requires_approval_before_push(self):
        self.seed();before=self.remote_oid()
        (self.work/'AGENTS.md').write_text('Changed rules\n')
        blocked=json.loads(self.hook().stdout)
        self.assertIn('additionalContext',blocked['hookSpecificOutput'])
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

    def test_new_ref_uses_fetched_base_without_a_local_base_approval(self):
        state=json.loads((self.work/'.git/workflow-policy-approvals.json').read_text())
        self.assertNotIn('public_bases',state)
        self.assertEqual(self.push('HEAD:refs/heads/feature').returncode,0)
        self.assertEqual(self.remote_oid('refs/heads/feature'),
                         self.git('rev-parse','HEAD').stdout.strip())

    def test_changes_already_in_base_need_no_feature_reapproval(self):
        (self.work/'AGENTS.md').write_text('Historical base rules\n')
        self.commit_all('historical policy change')
        (self.work/'AGENTS.md').write_text('Approved fixture rules\n')
        self.commit_all('historical restore')
        accepted_base=self.git('rev-parse','HEAD').stdout.strip()
        self.assertEqual(self.process(['git','--git-dir',str(self.remote),'fetch','-q',
            str(self.work),accepted_base+':refs/heads/main']).returncode,0)
        self.assertEqual(self.git('update-ref','refs/remotes/origin/main',
                                  accepted_base).returncode,0)
        self.write_commit('feature content\n','feature work')
        result=self.push('HEAD:refs/heads/feature')
        self.assertEqual(result.returncode,0,result.stderr)

    def test_develop_can_be_the_base_branch(self):
        self.assertEqual(self.process(['git','--git-dir',str(self.remote),'update-ref',
            'refs/heads/develop',self.base]).returncode,0)
        self.assertEqual(self.git('fetch','-q','origin','develop').returncode,0)
        self.assertEqual(self.git('update-ref','refs/remotes/origin/develop',self.base).returncode,0)
        self.assertEqual(self.git('symbolic-ref','--delete',
                                  'refs/remotes/origin/HEAD').returncode,0)
        result=self.git('-c','workflow.baseBranch=develop','push','origin',
                        'HEAD:refs/heads/feature')
        self.assertEqual(result.returncode,0,result.stderr)

    def test_other_destination_has_no_trusted_base(self):
        alternate=self.root/'alternate.git'
        self.assertEqual(self.process(['git','init','-q','--bare',str(alternate)]).returncode,0)
        self.assertEqual(self.process(['git','--git-dir',str(alternate),'fetch','-q',
            str(self.work),self.base+':refs/heads/main']).returncode,0)
        self.assertEqual(self.git('remote','add','alternate',str(alternate)).returncode,0)
        self.assertNotEqual(self.git('push','alternate','HEAD:refs/heads/feature').returncode,0)

    def test_unapproved_intermediate_after_base_is_not_hidden(self):
        (self.work/'AGENTS.md').write_text('Unapproved intermediate\n')
        self.commit_all('unapproved change')
        (self.work/'AGENTS.md').write_text('Approved fixture rules\n')
        self.commit_all('restore')
        self.assertNotEqual(self.push('HEAD:refs/heads/feature').returncode,0)
        self.assertIsNone(self.remote_oid('refs/heads/feature'))

    def test_base_ref_missing_locally_fails_closed(self):
        self.assertEqual(self.git('symbolic-ref','--delete',
                                  'refs/remotes/origin/HEAD').returncode,0)
        self.assertNotEqual(self.push('HEAD:refs/heads/feature').returncode,0)

    def test_tracking_ref_ahead_of_destination_is_rejected(self):
        ahead=self.git('rev-parse','HEAD').stdout.strip()
        self.assertEqual(self.git('update-ref','refs/remotes/origin/main',ahead).returncode,0)
        self.assertNotEqual(self.push('HEAD:refs/heads/feature').returncode,0)
        self.assertIsNone(self.remote_oid('refs/heads/feature'))

    def test_existing_ref_does_not_trust_its_old_oid_as_approval(self):
        self.seed()
        (self.work/'AGENTS.md').write_text('Unapproved remote feature rules\n')
        self.commit_all('unapproved feature policy')
        bad=self.git('rev-parse','HEAD').stdout.strip()
        (self.work/'AGENTS.md').write_text('Approved fixture rules\n')
        self.commit_all('restore policy')
        self.assertEqual(self.process(['git','--git-dir',str(self.remote),'fetch','-q',
            str(self.work),bad+':refs/heads/feature']).returncode,0)
        self.assertNotEqual(self.push('HEAD:refs/heads/feature').returncode,0)

    def test_fresh_clone_bootstraps_only_base_policy(self):
        self.seed()
        self.assertEqual(self.process(['git','--git-dir',str(self.remote),
            'symbolic-ref','HEAD','refs/heads/main']).returncode,0)
        fresh=self.root/'fresh'
        self.assertEqual(self.process(['git','clone','-q',str(self.remote),
                                       str(fresh)]).returncode,0)
        installer=[sys.executable,'scripts/install-gitleaks-hook.py']
        result=self.process(installer,cwd=fresh)
        self.assertEqual(result.returncode,0,result.stderr)
        state=fresh/'.git/workflow-policy-approvals.json'
        self.assertTrue(state.exists())
        state.unlink()
        result=self.process(installer,cwd=fresh)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(state.exists())
        (fresh/'AGENTS.md').write_text('Unapproved feature rules\n')
        state.unlink()
        self.assertNotEqual(self.process(installer,cwd=fresh).returncode,0)

    def test_guard_requires_native_hook_and_installer_in_policy(self):
        spec=importlib.util.spec_from_file_location('guard_source',
            BASE/'scripts/policy-guard.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        policy=json.loads((BASE/'config/publication-policy.json').read_text())
        for name in ('scripts/pre-push-gitleaks.py','scripts/install-gitleaks-hook.py'):
            altered=dict(policy,protected_paths=[p for p in policy['protected_paths'] if p!=name])
            with self.subTest(name=name), self.assertRaises(ValueError):
                module.paths_read(altered)

    def test_approved_hook_upgrade_refuses_foreign_copy(self):
        other=native.HookTest()
        other.setUp()
        try:
            self.assertEqual(other.push().returncode,0)
            (other.work/'scripts').mkdir()
            shutil.copy(BASE/'scripts/policy-guard.py',other.work/'scripts/policy-guard.py')
            shutil.copy(BASE/'scripts/install-gitleaks-hook.py',
                        other.work/'scripts/install-gitleaks-hook.py')
            old=b'#!/bin/sh\nexit 2\n'
            (other.work/'scripts/pre-push-gitleaks.py').write_bytes(old)
            hook=other.work/'.git/hooks/pre-push'
            hook.write_bytes(old)
            hook.chmod(0o700)
            (other.work/'config').mkdir()
            (other.work/'config/publication-policy.json').write_bytes(
                (BASE/'config/publication-policy.json').read_bytes())
            (other.work/'AGENTS.md').write_text('Approved fixture rules\n')
            self.assertEqual(other.git('add','.').returncode,0)
            self.assertEqual(other.git('commit','-qm','approved old hook').returncode,0)
            guard=other.work/'scripts/policy-guard.py'
            self.assertEqual(other.process([sys.executable,str(guard),'approve',
                '--approval-ref','old-hook'],cwd=other.work).returncode,0)
            new=(BASE/'scripts/pre-push-gitleaks.py').read_bytes()
            (other.work/'scripts/pre-push-gitleaks.py').write_bytes(new)
            self.assertEqual(other.git('add','.').returncode,0)
            self.assertEqual(other.git('commit','-qm','approved new hook').returncode,0)
            installer=[sys.executable,str(BASE/'scripts/install-gitleaks-hook.py'),
                       '--upgrade-approved']
            self.assertNotEqual(other.process(installer,cwd=other.work).returncode,0)
            self.assertEqual(hook.read_bytes(),old)
            self.assertEqual(other.process([sys.executable,str(guard),'approve',
                '--approval-ref','new-hook'],cwd=other.work).returncode,0)
            foreign=b'#!/bin/sh\nexit 3\n'
            hook.write_bytes(foreign)
            self.assertNotEqual(other.process(installer,cwd=other.work).returncode,0)
            self.assertEqual(hook.read_bytes(),foreign)
            hook.write_bytes(old)
            # An external installer may not carry bytes the target never approved.
            (other.work/'scripts/pre-push-gitleaks.py').write_bytes(b'#!/bin/sh\nexit 4\n')
            self.assertEqual(other.git('commit','-qam','approved other hook').returncode,0)
            self.assertEqual(other.process([sys.executable,str(guard),'approve',
                '--approval-ref','other-hook'],cwd=other.work).returncode,0)
            self.assertNotEqual(other.process(installer,cwd=other.work).returncode,0)
            self.assertEqual(hook.read_bytes(),old)
            (other.work/'scripts/pre-push-gitleaks.py').write_bytes(new)
            self.assertEqual(other.git('commit','-qam','approved new hook again').returncode,0)
            self.assertEqual(other.process([sys.executable,str(guard),'approve',
                '--approval-ref','new-hook-again'],cwd=other.work).returncode,0)
            self.assertEqual(other.process(installer,cwd=other.work).returncode,0)
            self.assertEqual(hook.read_bytes(),new)
            self.assertTrue(os.access(hook,os.X_OK))
        finally:
            other.doCleanups()

    def test_missing_state_and_malformed_event_deny(self):
        for payload in ('{}','[]','invalid json'):
            result=self.guard('hook',input=payload)
            self.assertEqual(result.returncode,0)
            self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'],'deny')
        (self.work/'.git/workflow-policy-approvals.json').unlink()
        self.assertEqual(json.loads(self.hook().stdout)['hookSpecificOutput']['permissionDecision'],'deny')
        self.assertNotEqual(self.push().returncode,0)
        self.assertEqual(self.remote_oid(),self.base)
        self.assertEqual(self.guard('approve','--approval-ref','recover-after-loss').returncode,0)
        self.assertEqual(self.guard('check').returncode,0)

    def test_dirty_approval_reuse_and_symlink_refused(self):
        self.assertEqual(self.guard('approve','--approval-ref','fixture-initial').returncode,0)
        (self.work/'AGENTS.md').write_text('dirty rules')
        self.assertNotEqual(self.guard('approve','--approval-ref','dirty').returncode,0)
        self.commit_all('update')
        self.assertNotEqual(self.guard('approve','--approval-ref','fixture-initial').returncode,0)
        (self.work/'AGENTS.md').unlink();(self.work/'AGENTS.md').symlink_to(self.work/'data')
        self.assertEqual(json.loads(self.hook().stdout)['hookSpecificOutput']['permissionDecision'],'deny')

    def test_claude_settings_change_requires_approval(self):
        (self.work/'.claude').mkdir()
        (self.work/'.claude/settings.json').write_text('{}\n')
        self.assertIn('additionalContext',json.loads(self.hook().stdout)['hookSpecificOutput'])
        self.commit_all('claude settings')
        self.assertEqual(self.guard('approve','--approval-ref','fixture-claude').returncode,0)
        self.assertEqual(self.hook().stdout,'')
        (self.work/'.claude/skills').mkdir()
        (self.work/'.claude/skills/linked').symlink_to('../../scripts')
        self.assertEqual(self.hook().stdout,'')

    def test_policy_must_protect_claude_settings(self):
        policy=json.loads((self.work/'config/publication-policy.json').read_text())
        policy['protected_paths'].remove('.claude/settings.json')
        (self.work/'config/publication-policy.json').write_text(json.dumps(policy))
        self.commit_all('drop claude')
        self.assertNotEqual(self.guard('approve','--approval-ref','fixture-drop').returncode,0)

    def test_skill_change_requires_approval(self):
        (self.work/'.agents/skills/sample').mkdir(parents=True)
        (self.work/'.agents/skills/sample/SKILL.md').write_text('changed\n')
        self.assertIn('additionalContext',json.loads(self.hook().stdout)['hookSpecificOutput'])

    def test_policy_must_protect_skills(self):
        policy=json.loads((self.work/'config/publication-policy.json').read_text())
        policy['protected_paths'].remove('.agents/skills')
        (self.work/'config/publication-policy.json').write_text(json.dumps(policy))
        self.commit_all('drop skills')
        self.assertNotEqual(self.guard('approve','--approval-ref','fixture-drop-skills').returncode,0)

    def test_new_branch_skips_history_already_on_remote(self):
        self.seed()
        (self.work/'AGENTS.md').write_text('Published before approval reset\n');self.commit_all('published')
        self.assertEqual(self.git('push','--no-verify','origin','HEAD:refs/heads/main').returncode,0)
        self.assertEqual(self.git('fetch','-q','origin').returncode,0)
        (self.work/'AGENTS.md').write_text('Approved fixture rules\n');self.commit_all('restore')
        self.assertEqual(self.push('HEAD:refs/heads/feature').returncode,0)
        (self.work/'AGENTS.md').write_text('Unapproved\n');self.commit_all('unapproved')
        self.assertNotEqual(self.push('HEAD:refs/heads/feature2').returncode,0)
        self.assertIsNone(self.remote_oid('refs/heads/feature2'))

    def test_new_branch_checks_history_known_only_to_other_remote(self):
        self.seed()
        other=self.root/'other.git'
        self.assertEqual(self.process(['git','init','-q','--bare',str(other)]).returncode,0)
        self.assertEqual(self.git('remote','add','other',str(other)).returncode,0)
        (self.work/'AGENTS.md').write_text('Unapproved elsewhere\n');self.commit_all('elsewhere')
        self.assertEqual(self.git('push','--no-verify','other','HEAD:refs/heads/main').returncode,0)
        self.assertEqual(self.git('fetch','-q','other').returncode,0)
        (self.work/'AGENTS.md').write_text('Approved fixture rules\n');self.commit_all('restore')
        self.assertNotEqual(self.push('HEAD:refs/heads/feature').returncode,0)
        self.assertIsNone(self.remote_oid('refs/heads/feature'))

    def allowed(self):
        return self.guard('check').returncode==0 and self.hook().stdout==''

    def test_ignored_cache_does_not_block_and_digest_is_stable(self):
        (self.work/'.gitignore').write_text('__pycache__/\n');self.commit_all('ignore')
        self.assertEqual(self.guard('approve','--approval-ref','fixture-ignore').returncode,0)
        module=importlib.util.spec_from_file_location('guard_under_test',self.work/'scripts/policy-guard.py')
        guard=importlib.util.module_from_spec(module);module.loader.exec_module(guard)
        paths=json.loads((self.work/'config/publication-policy.json').read_text())['protected_paths']
        cwd=os.getcwd();os.chdir(self.work)
        try:self.assertEqual(guard.snapshot(paths),guard.snapshot(paths,'HEAD'))
        finally:os.chdir(cwd)
        cache=self.work/'.agents/skills/sample/__pycache__'
        (self.work/'.agents/skills/sample/x.py').parent.mkdir(parents=True)
        (self.work/'.agents/skills/sample/x.py').write_text('x=1\n');self.commit_all('skill')
        self.assertEqual(self.guard('approve','--approval-ref','fixture-skill').returncode,0)
        cache.mkdir();(cache/'x.pyc').write_bytes(b'cache')
        self.assertTrue(self.allowed())
        self.assertEqual(self.guard('approve','--approval-ref','fixture-skill-2').returncode,0)
        (cache/'x.py').write_text('not bytecode\n')
        self.assertFalse(self.allowed())
        (cache/'x.py').unlink();(cache/'y.pyc').symlink_to('x.pyc')
        self.assertFalse(self.allowed())

    def test_gitignore_cannot_hide_new_protected_file(self):
        (self.work/'.gitignore').write_text('.agents/skills/evil/\n')
        (self.work/'.agents/skills/evil').mkdir(parents=True)
        (self.work/'.agents/skills/evil/SKILL.md').write_text('evil\n')
        self.assertFalse(self.allowed())

    def test_tracked_and_unignored_changes_still_denied(self):
        (self.work/'.gitignore').write_text('*.md\n')
        self.commit_all('ignore md')
        self.assertEqual(self.guard('approve','--approval-ref','fixture-md').returncode,0)
        self.assertTrue(self.allowed())
        (self.work/'AGENTS.md').write_text('tracked but ignored edit\n')
        self.assertFalse(self.allowed())
        self.assertEqual(self.git('checkout','--','AGENTS.md').returncode,0)
        (self.work/'scripts/policy-guard.py').rename(self.root/'moved.py')
        self.assertNotEqual(self.guard('check').returncode,0)
        (self.root/'moved.py').rename(self.work/'scripts/policy-guard.py')
        self.assertTrue(self.allowed())
        (self.work/'.agents/skills/new').mkdir(parents=True)
        (self.work/'.agents/skills/new/run.py').write_text('new\n')
        self.assertFalse(self.allowed())

    def test_local_excludes_and_self_ignoring_gitignore_denied(self):
        exclude=Path(self.git('rev-parse','--git-path','info/exclude').stdout.strip())
        if not exclude.is_absolute():exclude=self.work/exclude
        exclude.parent.mkdir(parents=True,exist_ok=True);exclude.write_text('hidden.py\n')
        (self.work/'.agents/skills/a').mkdir(parents=True)
        (self.work/'.agents/skills/a/hidden.py').write_text('x\n')
        self.assertFalse(self.allowed())
        (self.work/'.agents/skills/a/hidden.py').unlink()
        self.assertTrue(self.allowed())
        (self.work/'.agents/skills/a/.gitignore').write_text('*\n')
        self.assertFalse(self.allowed())
        (self.work/'.agents/skills/a/.gitignore').unlink()
        (self.work/'.agents/skills/a/link').symlink_to('../../../scripts')
        self.assertFalse(self.allowed())

    def test_linked_worktree_shares_approvals(self):
        other=self.root/'linked'
        self.assertEqual(self.git('worktree','add','-qb','other',str(other)).returncode,0)
        self.assertEqual(self.guard('check',cwd=other).returncode,0)
        (other/'AGENTS.md').write_text('unapproved')
        self.assertNotEqual(self.guard('check',cwd=other).returncode,0)
        self.assertEqual(self.guard('check').returncode,0)


if __name__=='__main__':
    unittest.main()
