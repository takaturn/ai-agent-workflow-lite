#!/usr/bin/env python3
"""Model-free fixtures for the informational protected change listing (#85)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1]/'.agents/scripts/protected-changes.py'
POLICY = '.agents/config/publication-policy.json'


class ProtectedChangesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)/'work'
        self.summary = Path(tmp.name)/'summary.md'
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GITHUB_STEP_SUMMARY=str(self.summary))
        self.git('init', '-q', str(self.work), cwd=tmp.name)
        self.write(POLICY, json.dumps({'version': 1, 'protected_paths': ['AGENTS.md', '.agents/skills', POLICY]}))
        self.write('AGENTS.md', 'rules\n')
        self.write('README.md', 'readme\n')
        self.base = self.commit('base')

    def git(self, *args, cwd=None):
        return subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c',
            'user.email=fixture@example.invalid', *args], cwd=cwd or self.work, env=self.env).decode().strip()

    def write(self, name, text):
        (self.work/name).parent.mkdir(parents=True, exist_ok=True)
        (self.work/name).write_text(text)

    def commit(self, message):
        self.git('add', '-A')
        self.git('commit', '-qm', message)
        return self.git('rev-parse', 'HEAD')

    def run_script(self, head):
        result = subprocess.run([sys.executable, str(SCRIPT), self.base, head], cwd=self.work,
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.summary.read_text(), result.stdout)
        self.summary.unlink()
        return result.stdout

    def test_listing_cases(self):
        self.write('README.md', 'changed\n')
        self.assertIn('None.', self.run_script(self.commit('unprotected only')))
        self.write('AGENTS.md', 'changed\n')
        self.write('.agents/skills/a/SKILL.md', 'new\n')
        self.write('.agents/skillset.md', 'prefix only, not protected\n')
        out = self.run_script(self.commit('protected'))
        self.assertIn('- `AGENTS.md`', out)
        self.assertIn('- `.agents/skills/a/SKILL.md`', out)
        self.assertNotIn('README.md', out)
        self.assertNotIn('skillset', out)
        # The PR drops AGENTS.md from its list; the base list still reports it.
        self.write(POLICY, json.dumps({'version': 1, 'protected_paths': ['README.md']}))
        out = self.run_script(self.commit('hide by editing the list'))
        for name in ('AGENTS.md', 'README.md', POLICY):
            self.assertIn(f'- `{name}`', out)

    def test_error_is_reported_with_exit_zero(self):
        self.assertIn('Not determined', self.run_script('no-such-revision'))
        # Unreadable lists on both sides are not reported as "None." (#85 review).
        self.write(POLICY, 'not json')
        self.write('AGENTS.md', 'changed\n')
        head = self.commit('broken list')
        self.git('checkout', '-q', '--detach', self.base)
        self.write(POLICY, json.dumps({'version': 1, 'protected_paths': 'AGENTS.md'}))
        self.base = self.commit('string list')
        out = self.run_script(head)
        self.assertIn('Not determined', out)

    def test_quoted_name_is_listed(self):
        self.write('.agents/skills/a"b\tc/x.md', 'new\n')
        out = self.run_script(self.commit('name with quote and tab'))
        self.assertIn('.agents/skills/a"b\tc/x.md', out)


if __name__ == '__main__':
    unittest.main()
