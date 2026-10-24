#!/usr/bin/env python3
"""Native hook tests using actual local pushes to disposable bare repositories."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
INSTALL = BASE/'scripts/install-gitleaks-hook.py'


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root/'work'
        self.remote = self.root/'receiver.git'
        self.bin = self.root/'bin'
        self.bin.mkdir()
        scanner = self.bin/'gitleaks'
        scanner.write_text('#!'+sys.executable+'\nimport os,sys\ndata=sys.stdin.buffer.read()\nprint("fixture-private-diagnostic")\nsys.exit(int(os.environ.get("SCAN_FAIL", "0")) or (1 if b"fixture-sensitive" in data else 0))\n')
        scanner.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.bin)+os.pathsep+os.environ['PATH'],
                        GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        self.process(['git','init','-q','--bare',str(self.remote)])
        self.process(['git','init','-q',str(self.work)])
        self.git('config','user.name','Fixture')
        self.git('config','user.email','fixture@example.invalid')
        self.git('remote','add','origin',str(self.remote))
        self.write_commit('public fixture\n','base')
        self.base=self.git('rev-parse','HEAD').stdout.strip()
        self.assertEqual(self.install().returncode,0)

    def process(self, command, **kwargs):
        return subprocess.run(command, env=self.env, text=True, capture_output=True, **kwargs)

    def git(self, *args, cwd=None):
        return self.process(['git',*args],cwd=cwd or self.work)

    def install(self, cwd=None):
        return self.process([sys.executable,str(INSTALL)],cwd=cwd or self.work)

    def write_commit(self, content, message):
        data=content.encode() if isinstance(content,str) else content
        (self.work/'data').write_bytes(data)
        self.assertEqual(self.git('add','data').returncode,0)
        result=self.git('commit','-qm',message)
        self.assertEqual(result.returncode,0,result.stderr)

    def remote_oid(self, ref='refs/heads/main'):
        r=self.process(['git','--git-dir',str(self.remote),'rev-parse','--verify',ref])
        return r.stdout.strip() if r.returncode==0 else None

    def push(self,*refs,cwd=None):
        return self.git('push','origin',*(refs or ('HEAD:refs/heads/main',)),cwd=cwd)

    def test_normal_push_and_secret_block_before_receiver_update(self):
        self.assertEqual(self.push().returncode,0)
        before=self.remote_oid()
        self.write_commit('fixture-sensitive','unsafe')
        result=self.push()
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.remote_oid(),before)
        self.assertNotIn('fixture-sensitive',result.stdout+result.stderr)
        self.assertNotIn('fixture-private-diagnostic',result.stdout+result.stderr)

    def test_deleted_content_and_multiple_refs_still_block(self):
        self.assertEqual(self.push().returncode,0)
        self.write_commit('fixture-sensitive','temporary')
        unsafe=self.git('rev-parse','HEAD').stdout.strip()
        self.write_commit('public fixture again','removed')
        result=self.push('HEAD:refs/heads/new','HEAD:refs/heads/other')
        self.assertNotEqual(result.returncode,0)
        self.assertIsNone(self.remote_oid('refs/heads/new'))
        self.assertIsNone(self.remote_oid('refs/heads/other'))
        self.assertNotEqual(self.push(unsafe+':refs/heads/unsafe').returncode,0)

    def test_scanner_error_and_absence_block(self):
        for code in ('2','127'):
            self.env['SCAN_FAIL']=code
            self.assertNotEqual(self.push().returncode,0)
            self.assertIsNone(self.remote_oid())
        self.env.pop('SCAN_FAIL')
        (self.bin/'gitleaks').unlink()
        for binary in ('git','python3'):
            (self.bin/binary).symlink_to(shutil.which(binary))
        self.env['PATH']=str(self.bin)
        self.assertNotEqual(self.push().returncode,0)
        self.assertIsNone(self.remote_oid())

    def test_force_push_and_annotated_tag_message_block(self):
        self.assertEqual(self.push().returncode,0)
        self.assertEqual(self.git('tag','-a','bad-tag','-m','fixture-sensitive').returncode,0)
        self.assertNotEqual(self.push('refs/tags/bad-tag').returncode,0)
        self.assertIsNone(self.remote_oid('refs/tags/bad-tag'))
        self.git('checkout','-q','--orphan','unrelated')
        self.write_commit('fixture-sensitive','unrelated root')
        self.assertNotEqual(self.push('--force','HEAD:refs/heads/main').returncode,0)
        self.assertEqual(self.remote_oid(),self.base)

    def test_binary_blocks_and_deletion_sends_no_content(self):
        self.assertEqual(self.push().returncode,0)
        self.write_commit(b'public\x00binary','binary')
        self.assertNotEqual(self.push().returncode,0)
        self.assertEqual(self.remote_oid(),self.base)
        self.env['SCAN_FAIL']='2'
        self.assertEqual(self.push(':refs/heads/main').returncode,0)
        self.assertIsNone(self.remote_oid())

    def test_install_idempotence_and_linked_worktree(self):
        self.assertEqual(self.install().returncode,0)
        linked=self.root/'linked'
        self.assertEqual(self.git('worktree','add','-qb','linked',str(linked),'HEAD').returncode,0)
        self.assertEqual(self.install(linked).returncode,0)
        self.assertEqual(self.push('HEAD:refs/heads/linked',cwd=linked).returncode,0)
        (linked/'data').write_text('fixture-sensitive')
        self.git('add','data',cwd=linked);self.git('commit','-qm','unsafe linked',cwd=linked)
        self.assertNotEqual(self.push('HEAD:refs/heads/linked',cwd=linked).returncode,0)
        self.assertEqual(self.remote_oid('refs/heads/linked'),self.base)

    def test_existing_hook_and_custom_config_are_not_overwritten(self):
        hook=self.work/'.git/hooks/pre-push'
        hook.write_text('#!/bin/sh\nexit 42\n')
        before=hook.read_bytes()
        self.assertNotEqual(self.install().returncode,0)
        self.assertEqual(hook.read_bytes(),before)
        hook.unlink()
        self.git('config','core.hooksPath',str(self.root/'custom'))
        self.assertNotEqual(self.install().returncode,0)
        self.assertFalse(hook.exists())

    def test_merge_only_content_blocks(self):
        self.write_commit('first','first')
        first=self.git('rev-parse','HEAD').stdout.strip()
        self.git('checkout','-q','--detach',self.base)
        self.write_commit('second','second')
        second=self.git('rev-parse','HEAD').stdout.strip()
        (self.work/'data').write_text('fixture-sensitive')
        self.git('add','data')
        tree=self.git('write-tree').stdout.strip()
        merge=self.git('commit-tree',tree,'-p',first,'-p',second,'-m','merge').stdout.strip()
        result=self.push(merge+':refs/heads/merge')
        self.assertNotEqual(result.returncode,0)
        self.assertIsNone(self.remote_oid('refs/heads/merge'))

    def test_raw_commit_header_secret_blocks_before_receiver_update(self):
        self.assertEqual(self.push().returncode, 0)
        tree = self.git('rev-parse', 'HEAD^{tree}').stdout.strip()
        raw = (f'tree {tree}\nparent {self.base}\n'
               'author Fixture <fixture@example.invalid> 1700000000 +0000\n'
               'committer Fixture <fixture@example.invalid> 1700000000 +0000\n'
               'x-private fixture-sensitive\n\npublic commit message\n')
        result = subprocess.run(['git', 'hash-object', '-t', 'commit', '-w', '--stdin'],
            input=raw, cwd=self.work, env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        oid = result.stdout.strip()
        pushed = self.push(oid+':refs/heads/main')
        self.assertNotEqual(pushed.returncode, 0)
        self.assertIn('pre-push:', pushed.stderr)
        self.assertEqual(self.remote_oid(), self.base)
        self.assertNotIn('fixture-sensitive', pushed.stdout+pushed.stderr)

    def test_replace_view_cannot_hide_original_content(self):
        self.assertEqual(self.push().returncode, 0)
        self.write_commit('fixture-sensitive', 'original')
        original = self.git('rev-parse', 'HEAD').stdout.strip()
        public_tree = self.git('rev-parse', self.base+'^{tree}').stdout.strip()
        substitute = self.git('commit-tree', public_tree, '-p', self.base, '-m', 'public replacement').stdout.strip()
        self.assertEqual(self.git('replace', original, substitute).returncode, 0)
        result = self.push()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.remote_oid(), self.base)

    def test_color_configuration_cannot_hide_binary_marker(self):
        self.assertEqual(self.push().returncode, 0)
        self.git('config', 'color.ui', 'always')
        self.write_commit(b'public\x00binary', 'binary')
        self.assertNotEqual(self.push().returncode, 0)
        self.assertEqual(self.remote_oid(), self.base)

    def test_shallow_history_is_refused(self):
        (self.work/'.git/shallow').write_text(self.base+'\n')
        result = self.push()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('pre-push:', result.stderr)
        self.assertIsNone(self.remote_oid())


if __name__=='__main__':
    unittest.main()
