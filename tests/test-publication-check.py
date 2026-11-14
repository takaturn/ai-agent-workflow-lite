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

GATE = Path(__file__).resolve().parents[1]/'.agents/scripts/publication-check.py'
POLICY = GATE.parents[1]/'config/publication-policy.json'


class PolicyRoot:
    """A plain repository with the real protected path list; no approval state (#85)."""
    def __init__(self, test):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name).resolve()/'work'
        subprocess.run(['git', 'init', '-q', str(self.work)], check=True)
        (self.work/'.agents/config').mkdir(parents=True)
        (self.work/'.agents/config/publication-policy.json').write_bytes(POLICY.read_bytes())
        (self.work/'AGENTS.md').write_text('Fixture rules\n')
        self.commit_all('policy')

    def commit_all(self, message):
        for args in (['add', '.'], ['commit', '-qm', message]):
            subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                            *args], cwd=self.work, check=True)


class PublicationTest(unittest.TestCase):
    def setUp(self):
        self.policy = PolicyRoot(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root/'bin'
        self.bin.mkdir()
        scanner = self.bin/'gitleaks'
        scanner.write_text('#!/usr/bin/env python3\nimport os,sys\ndata=sys.stdin.buffer.read()\nif os.environ.get("SCAN_CHANGES"): open(os.environ["SCAN_CHANGES"],"w").write("Changed during scan\\n")\nprint("scanner diagnostic must stay private")\nsys.exit(int(os.environ.get("SCAN_EXIT", "0")) or (1 if b"fixture-sensitive" in data else 0))\n')
        scanner.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.bin)+os.pathsep+os.environ['PATH'],
                        XDG_STATE_HOME=str(self.root/'state'))
        self.body = self.root/'body.txt'
        self.body.write_text('public fixture\n')
        self.marker = self.root/'published'
        self.command = [sys.executable, '-c', 'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("sent")', str(self.marker)]

    def invoke(self, mode, *extra, command=None):
        return subprocess.run([sys.executable, str(GATE), mode, '--policy-root', str(self.policy.work), '--target', 'fixture:local',
            '--file', str(self.body), *extra, '--', *(command or self.command)],
            env=self.env, cwd=self.root, capture_output=True, text=True)

    def prepare(self):
        result = self.invoke('prepare', '--context-reviewed')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('diagnostic', result.stdout+result.stderr)
        return result.stdout.strip()

    def test_protected_change_after_prepare_blocks_dispatch(self):
        receipt = self.prepare()
        (self.policy.work/'AGENTS.md').write_text('Changed after prepare\n')
        self.assertNotEqual(self.invoke('publish','--receipt',receipt).returncode,0)
        self.assertFalse(self.marker.exists())
        self.policy.commit_all('a commit does not restore the snapshot')
        self.assertNotEqual(self.invoke('publish','--receipt',receipt).returncode,0)
        self.assertFalse(self.marker.exists())
        # No local approval exists: a fresh prepare of the changed policy publishes.
        new_receipt=self.prepare()
        self.assertEqual(self.invoke('publish','--receipt',new_receipt).returncode,0)
        # The scanner changes the policy synchronously during the publish scan,
        # so only the re-check just before dispatch can block it (#62).
        self.marker.unlink()
        last_receipt=self.prepare()
        self.env['SCAN_CHANGES']=str(self.policy.work/'AGENTS.md')
        self.assertNotEqual(self.invoke('publish','--receipt',last_receipt).returncode,0)
        self.assertEqual((self.policy.work/'AGENTS.md').read_text(),'Changed during scan\n')
        self.assertFalse(self.marker.exists())

    def test_policy_root_must_be_a_worktree_root(self):
        # A subdirectory with its own policy copy is still not a policy root.
        nested = self.policy.work/'nested/.agents/config'
        nested.mkdir(parents=True)
        (nested/'publication-policy.json').write_bytes(POLICY.read_bytes())
        for root in (self.policy.work/'nested', self.policy.work.parent, 'list-policy'):
            if root == 'list-policy':
                (self.policy.work/'.agents/config/publication-policy.json').write_text('[]')
                root = self.policy.work
            result = subprocess.run([sys.executable, str(GATE), 'prepare', '--policy-root', str(root),
                '--target', 'fixture:local', '--file', str(self.body), '--context-reviewed', '--', *self.command],
                env=self.env, cwd=self.root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, root)
            self.assertIn('publication blocked', result.stderr)

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
        git, base = self.git_fixture()
        self.body.write_text('second commit\n')
        git('add', 'body.txt'); git('commit', '-qm', 'second')
        with tempfile.TemporaryDirectory() as state:
            self.env['XDG_STATE_HOME'] = state
            # base..HEAD..HEAD has valid endpoints; only the '..' count rejects it (#62).
            for revision in ('--no-patch', 'HEAD', '..HEAD', 'HEAD..', 'HEAD...HEAD', 'HEAD..--no-patch',
                             base+'..HEAD..HEAD'):
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
        # NUL in the message: plain hash-object refuses it (fsck), --literally stores it (#62).
        raw = (f'tree {tree}\nparent {base}\n'
               'author Fixture <fixture@example.invalid> 1700000000 +0000\n'
               'committer Fixture <fixture@example.invalid> 1700000000 +0000\n'
               '\npublic\0message\n')
        oid = subprocess.check_output(['git', 'hash-object', '-t', 'commit', '--literally', '-w', '--stdin'],
            input=raw.encode(), cwd=self.root).decode().strip()
        self.assertIn(b'\0', subprocess.check_output(['git', 'cat-file', 'commit', oid], cwd=self.root))
        with tempfile.TemporaryDirectory() as state:
            self.env['XDG_STATE_HOME'] = state
            result = self.invoke('prepare', '--context-reviewed', '--git-range', base+'..'+oid)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.marker.exists())

    def test_missing_scanner_blocks(self):
        (self.bin/'gitleaks').unlink()
        (self.bin/'git').symlink_to(shutil.which('git'))
        self.env['PATH'] = str(self.bin)
        self.assertNotEqual(self.invoke('prepare', '--context-reviewed').returncode, 0)
        self.assertFalse(self.marker.exists())


if __name__ == '__main__':
    unittest.main()
