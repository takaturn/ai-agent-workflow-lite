#!/usr/bin/env python3
"""Model-free publication gate tests; all dispatches are local fixture commands."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest

GATE = Path(__file__).resolve().parents[1]/'scripts/publication-check.py'


class PublicationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root/'bin'
        self.bin.mkdir()
        scanner = self.bin/'gitleaks'
        scanner.write_text('#!/usr/bin/env python3\nimport os,sys\ndata=sys.stdin.buffer.read()\nprint("scanner diagnostic must stay private")\nsys.exit(int(os.environ.get("SCAN_EXIT", "0")) or (1 if b"fixture-sensitive" in data else 0))\n')
        scanner.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.bin)+os.pathsep+os.environ['PATH'],
                        XDG_STATE_HOME=str(self.root/'state'))
        self.body = self.root/'body.txt'
        self.body.write_text('public fixture\n')
        self.marker = self.root/'published'
        self.command = [sys.executable, '-c', 'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("sent")', str(self.marker)]

    def invoke(self, mode, *extra, command=None):
        return subprocess.run([sys.executable, str(GATE), mode, '--target', 'fixture:local',
            '--file', str(self.body), *extra, '--', *(command or self.command)],
            env=self.env, cwd=self.root, capture_output=True, text=True)

    def prepare(self):
        result = self.invoke('prepare', '--context-reviewed')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('diagnostic', result.stdout+result.stderr)
        return result.stdout.strip()

    def test_unreviewed_and_missing_receipt_block(self):
        self.assertNotEqual(self.invoke('prepare').returncode, 0)
        self.assertNotEqual(self.invoke('publish').returncode, 0)
        self.assertFalse(self.marker.exists())

    def test_success_consumed_before_retry(self):
        receipt = self.prepare()
        self.assertEqual(self.invoke('publish', '--receipt', receipt).returncode, 0)
        self.assertTrue(self.marker.exists())
        self.marker.unlink()
        self.assertNotEqual(self.invoke('publish', '--receipt', receipt).returncode, 0)
        self.assertFalse(self.marker.exists())

    def test_changed_body_or_command_blocks(self):
        receipt = self.prepare()
        self.body.write_text('changed body')
        self.assertNotEqual(self.invoke('publish', '--receipt', receipt).returncode, 0)
        self.body.write_text('public fixture\n')
        self.assertNotEqual(self.invoke('publish', '--receipt', receipt, command=self.command+['changed']).returncode, 0)
        self.assertFalse(self.marker.exists())

    def test_detection_and_scanner_failure_block_without_output(self):
        self.body.write_text('fixture-sensitive')
        result = self.invoke('prepare', '--context-reviewed')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('fixture-sensitive', result.stdout+result.stderr)
        self.body.write_text('public fixture\n')
        receipt = self.prepare()
        for code in ('1', '2', '127'):
            self.env['SCAN_EXIT'] = code
            result = self.invoke('publish', '--receipt', receipt)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('diagnostic', result.stdout+result.stderr)
            self.assertFalse(self.marker.exists())

    def test_transient_secret_and_commit_message_in_range(self):
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        def git(*args):
            return subprocess.check_output(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid',*args],cwd=self.root)
        git('add','body.txt');git('commit','-qm','base')
        base=git('rev-parse','HEAD').decode().strip()
        self.body.write_text('fixture-sensitive');git('add','body.txt');git('commit','-qm','temporary')
        self.body.write_text('public fixture\n');git('add','body.txt');git('commit','-qm','removed')
        # State home is now under a Git repo: use an external fixture directory.
        with tempfile.TemporaryDirectory() as external:
            self.env['XDG_STATE_HOME']=external
            result=self.invoke('prepare','--context-reviewed','--git-range',base+'..HEAD')
            self.assertNotEqual(result.returncode,0)
            self.assertFalse(self.marker.exists())
        # A new non-existent state directory inside Git must also be refused.
        self.env["XDG_STATE_HOME"] = str(self.root/"new-state")
        self.assertNotEqual(self.invoke('prepare','--context-reviewed').returncode,0)

    def git_fixture(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        def git(*args):
            return subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c',
                'user.email=fixture@example.invalid', *args], cwd=self.root)
        git('add', 'body.txt'); git('commit', '-qm', 'base')
        return git, git('rev-parse', 'HEAD').decode().strip()

    def test_git_options_and_invalid_ranges_block(self):
        self.git_fixture()
        with tempfile.TemporaryDirectory() as state:
            self.env['XDG_STATE_HOME'] = state
            for revision in ('--no-patch', 'HEAD', '..HEAD', 'HEAD..', 'HEAD...HEAD', 'HEAD..--no-patch'):
                result = self.invoke('prepare', '--context-reviewed', '--git-range='+revision)
                self.assertNotEqual(result.returncode, 0, revision)
            self.assertFalse(self.marker.exists())

    def test_merge_only_content_is_scanned(self):
        git, base = self.git_fixture()
        self.body.write_text('first branch')
        git('add', 'body.txt'); git('commit', '-qm', 'first')
        first = git('rev-parse', 'HEAD').decode().strip()
        git('checkout', '-q', '--detach', base)
        self.body.write_text('second branch')
        git('add', 'body.txt'); git('commit', '-qm', 'second')
        second = git('rev-parse', 'HEAD').decode().strip()
        # Content is introduced only by merge resolution, absent from parents.
        self.body.write_text('fixture-sensitive')
        git('add', 'body.txt')
        tree = git('write-tree').decode().strip()
        merge = git('commit-tree', tree, '-p', first, '-p', second, '-m', 'merge').decode().strip()
        self.body.write_text('public fixture\n')
        with tempfile.TemporaryDirectory() as state:
            self.env['XDG_STATE_HOME'] = state
            result = self.invoke('prepare', '--context-reviewed', '--git-range', base+'..'+merge)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.marker.exists())

    def test_binary_file_and_git_patch_block(self):
        self.body.write_bytes(b'public\x00binary')
        self.assertNotEqual(self.invoke('prepare', '--context-reviewed').returncode, 0)
        self.body.write_text('public fixture\n')
        git, base = self.git_fixture()
        self.body.write_bytes(b'public\x00binary')
        git('add', 'body.txt'); git('commit', '-qm', 'binary')
        self.body.write_text('public fixture\n')
        with tempfile.TemporaryDirectory() as state:
            self.env['XDG_STATE_HOME'] = state
            result = self.invoke('prepare', '--context-reviewed', '--git-range', base+'..HEAD')
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.marker.exists())

    def test_binary_marker_in_text_diff_is_not_a_binary_patch(self):
        git, base = self.git_fixture()
        self.body.write_text('literal Binary files example differ\n')
        git('add', 'body.txt'); git('commit', '-qm', 'ordinary text')
        with tempfile.TemporaryDirectory() as state:
            self.env['XDG_STATE_HOME'] = state
            result = self.invoke('prepare', '--context-reviewed', '--git-range', base+'..HEAD')
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_raw_commit_header_is_scanned(self):
        git, base = self.git_fixture()
        tree = git('rev-parse', 'HEAD^{tree}').decode().strip()
        raw = (f'tree {tree}\nparent {base}\n'
               'author Fixture <fixture@example.invalid> 1700000000 +0000\n'
               'committer Fixture <fixture@example.invalid> 1700000000 +0000\n'
               'x-private fixture-sensitive\n\npublic message\n')
        oid = subprocess.check_output(['git', 'hash-object', '-t', 'commit', '-w', '--stdin'],
            input=raw.encode(), cwd=self.root).decode().strip()
        with tempfile.TemporaryDirectory() as state:
            self.env['XDG_STATE_HOME'] = state
            result = self.invoke('prepare', '--context-reviewed', '--git-range', base+'..'+oid)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('publication blocked', result.stderr)
            self.assertFalse(self.marker.exists())

    def test_missing_scanner_blocks(self):
        (self.bin/'gitleaks').unlink()
        (self.bin/'git').symlink_to(shutil.which('git'))
        self.env['PATH'] = str(self.bin)
        self.assertNotEqual(self.invoke('prepare', '--context-reviewed').returncode, 0)
        self.assertFalse(self.marker.exists())


if __name__ == '__main__':
    unittest.main()
