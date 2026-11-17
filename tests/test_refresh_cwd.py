"""A native cache replacement must not invalidate a new requirement's cwd."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = r'''
import json, os, shutil, subprocess, sys
from pathlib import Path
from unittest.mock import patch
from plugin_runtime import cli
from tests.test_manager import FakeBackend

cache = Path.cwd()
home = cache.parent / 'home'
home.mkdir(exist_ok=True)
backend = FakeBackend(home)
original_install = backend.install
def refresh():
    backend.refresh_calls += 1
    shutil.rmtree(cache)  # Only this test's own empty temporary cache directory.
def install(release):
    # The real installer launches npm without a cwd; test the same inheritance.
    Path.cwd()
    child = subprocess.run([sys.executable, '-c', 'import os; print(os.getcwd())'],
                           capture_output=True, text=True)
    if child.returncode: raise OSError('CHILD_CWD_INVALID')
    if sys.argv[1] == 'fail': raise ValueError('INSTALL_FAILED')
    original_install(release)
backend.refresh = refresh
backend.install = install
with patch.object(cli.manager, 'Backend', return_value=backend):
    result = cli.main(['new', '--home', '../home', '--workspace', '../task',
                       '--loaded-plugin-version', '0.2.0'])
raise SystemExit(result)
'''


class RefreshCwdTests(unittest.TestCase):
    def attempt(self, root, mode):
        cache = root / 'cache'
        cache.mkdir()
        # Test-only override also exercises the actual built plugin module,
        # without adding a synthetic transport switch to the production CLI.
        plugin = Path(os.environ.get('TEST_PLUGIN_ROOT', ROOT)).resolve()
        env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(plugin), str(ROOT))),
                   PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run([sys.executable, '-B', '-c', SCRIPT, mode], cwd=cache,
                              env=env, capture_output=True, text=True, timeout=20)

    def test_cache_replacement_keeps_relative_workspace_and_subprocess_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            result = self.attempt(root, 'success')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'ready')
            self.assertTrue((root / 'task/.prototype-workflow/requirement.json').is_file())
            self.assertFalse((root / 'home/task').exists())

    def test_failure_has_no_binding_and_retry_works_without_touching_old_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            old = root / 'old-requirement.json'
            old.write_bytes(b'{"pinned":"previous"}\n')
            result = self.attempt(root, 'fail')
            self.assertEqual(result.returncode, 1)
            failure = json.loads(result.stderr.splitlines()[-1])
            self.assertEqual(failure['error'], 'INSTALL_FAILED')
            self.assertTrue(Path(failure['record_path']).is_file())
            self.assertFalse((root / 'task/.prototype-workflow/requirement.json').exists())
            retry = self.attempt(root, 'success')
            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertEqual(old.read_bytes(), b'{"pinned":"previous"}\n')


class ArchiveImportTests(unittest.TestCase):
    def test_normal_python_import_does_not_write_into_immutable_kit(self):
        plugin = Path(os.environ.get('TEST_PLUGIN_ROOT', ROOT / 'plugins/prototype-workflow')).resolve()
        with tempfile.TemporaryDirectory() as tmp:
            module_root = Path(os.environ.get('TEST_PLUGIN_ROOT', ROOT)).resolve()
            env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(module_root), str(ROOT))))
            env.pop('PYTHONDONTWRITEBYTECODE', None)
            script = r'''
import sys
from pathlib import Path
from plugin_runtime.manager import Backend
assert not sys.dont_write_bytecode
backend=Backend(Path(sys.argv[1]).resolve(),Path(sys.argv[2]).resolve())
release=backend.approved()
package=backend.prepare_package(release)
assert not list(package.rglob('*.pyc'))
assert not sys.dont_write_bytecode
assert backend.prepare_package(release)==package
try:
    with backend.kit(package):
        assert sys.dont_write_bytecode
        raise RuntimeError('test-only')
except RuntimeError as error:
    assert str(error)=='test-only'
assert not sys.dont_write_bytecode
'''
            result = subprocess.run([sys.executable, '-c', script, tmp, str(plugin)],
                                    env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
