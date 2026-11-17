"""Actual entry routing with explicit simulated installation, never a live run."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from plugin_runtime import cli, manager
from tests.test_manager import FakeBackend


class CandidateEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.backend = FakeBackend(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, command, workspace, *args):
        out, err = io.StringIO(), io.StringIO()
        previous = Path.cwd()
        try:
            with patch.object(cli.manager, 'Backend', return_value=self.backend), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = cli.main([command, '--home', str(self.home), '--workspace', str(workspace), *args])
            return status, out.getvalue(), err.getvalue()
        finally:
            os.chdir(previous)

    def test_new_reload_resume_and_run_keep_old_requirement_pinned(self):
        old = self.root / 'old'
        status, output, error = self.call('new', old, '--loaded-plugin-version', '0.2.0')
        self.assertEqual(status, 0, error)
        old_binding = json.loads(output)['binding']
        old_bytes = (old / '.prototype-workflow/requirement.json').read_bytes()
        self.backend.publish('0.2.1')
        new = self.root / 'new'
        status, output, error = self.call('new', new, '--loaded-plugin-version', '0.2.0')
        self.assertEqual(status, 0, error)
        self.assertEqual(json.loads(output)['status'], 'reload_required')
        self.assertFalse((new / '.prototype-workflow/requirement.json').exists())
        status, output, error = self.call('new', new, '--loaded-plugin-version', '0.2.1')
        self.assertEqual(status, 0, error)
        self.assertEqual(json.loads(output)['binding']['distribution_version'], '0.2.1')
        self.backend.refresh_calls = 0
        status, output, error = self.call('resume', old)
        self.assertEqual(status, 0, error)
        self.assertEqual(json.loads(output)['binding'], old_binding)
        calls = []
        def invoke(binding, arguments):
            calls.append((binding, arguments, Path.cwd()))
            return 0
        self.backend.invoke = invoke
        expected_cwd = Path.cwd()
        status, _, error = self.call('run', old, '--', 'prototype', 'start', '--prd', 'relative-prd.md')
        self.assertEqual(status, 0, error)
        self.assertEqual(calls, [(old_binding, ['prototype', 'start', '--prd', 'relative-prd.md'], expected_cwd)])
        self.assertEqual(self.backend.refresh_calls, 0)
        self.assertEqual((old / '.prototype-workflow/requirement.json').read_bytes(), old_bytes)

    def test_integrity_failure_does_not_fallback_or_create_binding(self):
        self.call('new', self.root / 'old', '--loaded-plugin-version', '0.2.0')
        self.backend.fail = ValueError('INTEGRITY')
        new = self.root / 'new'
        status, _, error = self.call('new', new, '--loaded-plugin-version', '0.2.0')
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(error)['error'], 'INTEGRITY')
        self.assertFalse((new / '.prototype-workflow/requirement.json').exists())

    def test_run_rejects_non_prototype_command(self):
        work = self.root / 'task'
        self.call('new', work, '--loaded-plugin-version', '0.2.0')
        self.backend.invoke = lambda *_: self.fail('executor must not be called')
        status, _, error = self.call('run', work, '--', 'shell')
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(error)['error'], 'PROTOTYPE_ONLY')

    def test_production_refresh_rejects_local_source_and_wrong_remote(self):
        # No actual Codex/network process. Execute the real trust decision.
        backend = manager.Backend(self.home)
        installed = {'pluginId': 'prototype-workflow@qqx-prototype',
                     'installedPath': str(self.home / '.codex/plugins/cache/candidate')}
        for source in ({'sourceType': 'local', 'source': '/local/candidate'},
                       {'sourceType': 'git', 'source': 'https://example.invalid/untrusted.git'}):
            listed = {'installed': [{'pluginId': installed['pluginId'], 'marketplaceSource': source}]}
            with self.subTest(source=source), patch.object(backend, 'codex', side_effect=[{}, installed, listed]), patch.object(backend, 'command') as command:
                with self.assertRaisesRegex(ValueError, 'UNTRUSTED_SOURCE'):
                    backend.refresh()
                command.assert_not_called()

    def test_candidate_archive_and_extracted_tree_fail_closed(self):
        candidate = Path(os.environ.get('TEST_PLUGIN_ROOT', 'plugins/prototype-workflow')).resolve()
        backend = manager.Backend(self.home, candidate)
        release = backend.approved()
        package = backend.prepare_package(release)
        with (package / 'prototype-kit.py').open('ab') as handle:
            handle.write(b'\n# test drift\n')
        with self.assertRaisesRegex(ValueError, 'PACKAGE_TREE_DRIFT'):
            backend.prepare_package(release)


if __name__ == '__main__':
    unittest.main()
