#!/usr/bin/env python3
"""Actual Git/Gitleaks/namespace acceptance against a disposable local receiver."""
import os
from pathlib import Path
import runpy
import secrets
import shutil
import subprocess
import unittest

def fixture_type():
    return runpy.run_path(str(Path(__file__).with_name('test-publisher.py')))['PublisherTest']


class RealPublisherTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(shutil.which('gitleaks'), 'actual Gitleaks required')
        previous = os.environ.get('PUBLISHER_TEST_REAL_SCANNER')
        os.environ['PUBLISHER_TEST_REAL_SCANNER'] = '1'
        self.fixture = fixture_type()('test_git_push_only_after_scan_and_old_ref_match')
        try:
            self.fixture.setUp()
        finally:
            if previous is None:os.environ.pop('PUBLISHER_TEST_REAL_SCANNER', None)
            else:os.environ['PUBLISHER_TEST_REAL_SCANNER'] = previous
        self.addCleanup(self.fixture.doCleanups)

    def test_actual_scanner_allows_clean_snapshot(self):
        fixture = self.fixture
        request = fixture.git_request('public fixture')
        envelope = fixture.approve(request)
        result = fixture.submit(envelope)
        self.assertEqual(result.returncode, 0, 'clean input blocked by actual scanner/transport')
        oid = subprocess.check_output(['git','--git-dir',str(fixture.receiver),
            'rev-parse','refs/heads/main'], text=True).strip()
        self.assertEqual(oid, request['payload']['oid'])

    def test_actual_scanner_blocks_generated_nonfunctional_credential(self):
        fixture = self.fixture
        content = 'nonfunctional fixture = ghp_'+secrets.token_hex(18)
        envelope = fixture.approve(fixture.git_request(content))
        result = fixture.submit(envelope)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(content, result.stdout+result.stderr)
        refs = subprocess.run(['git','--git-dir',str(fixture.receiver),'show-ref'], capture_output=True)
        self.assertNotEqual(refs.returncode, 0)


if __name__ == '__main__':
    unittest.main()
