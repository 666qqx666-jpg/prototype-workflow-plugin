"""Private-entry controls; setup/installation is explicitly simulated here."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from plugin_runtime import local_trial, manager, storage
from tests.test_manager import FakeBackend


class LocalTrialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.plugin = self.root / 'plugin'
        (self.plugin / 'release').mkdir(parents=True)
        (self.plugin / 'packages').mkdir()
        self.archive = self.plugin / 'packages/test.zip'
        self.archive.write_bytes(b'test-only archive; install is simulated')
        self.release = {'schema_version': 1, 'distribution_version': '0.2.6',
            'plugin_name': 'prototype-workflow', 'marketplace_name': 'qqx-prototype',
            'package_path': 'packages/test.zip', 'package_sha256': self.sha(),
            'skill_sha256': 'a' * 64, 'pack_sha256': 'b' * 64}
        (self.plugin / 'release/approved.json').write_text(json.dumps(self.release))
        (self.plugin / 'plugin.json').write_text(json.dumps({'name': 'prototype-workflow', 'version': '0.2.6'}))
        self.user = self.root / 'user.json'
        self.user.write_text(json.dumps({'schema_version': 1, 'ssh_user': 'synthetic', 'ssh_identity': '/unused/.ssh/key'}))
        self.user.chmod(0o600)
        self.args = dict(plugin_root=self.plugin, trial_root=self.root / 'trial', version='0.2.6',
                         package_sha256=self.sha(), company_profile=self.root / 'profile.json', user_config=self.user)
        self.backends = []

    def tearDown(self):
        self.tmp.cleanup()

    def sha(self):return hashlib.sha256(self.archive.read_bytes()).hexdigest()

    @contextlib.contextmanager
    def simulated_install(self, after_setup=None, verify=None):
        def backend(home, root):
            b = FakeBackend(home); b.publish('0.2.6')
            b.approved = lambda: self.release
            b.refresh = lambda: self.fail('private entry must not refresh public source')
            if verify is not None:b.verify_requirement = lambda path: verify(b, path)
            self.backends.append(b)
            return b
        def setup(**kwargs):
            self.assertEqual(kwargs['expected_release'], self.release)
            self.assertEqual(Path(os.environ['HOME']), self.args['trial_root'] / 'home')
            self.assertEqual(Path.cwd(), self.args['trial_root'] / 'home')
            if after_setup:after_setup(kwargs)
            return {'status': 'ready'}
        with patch.object(manager, 'Backend', side_effect=backend), patch.object(manager, 'setup', side_effect=setup):
            yield

    def start(self, **overrides):return local_trial.start_trial(**dict(self.args, **overrides))

    def test_ready_uses_selected_version_real_binding_publisher_and_restores_environment(self):
        before = dict(os.environ); cwd = Path.cwd()
        with self.simulated_install():result = self.start()
        self.assertEqual(result['mode'], 'private-candidate')
        self.assertEqual(result['binding']['distribution_version'], '0.2.6')
        path = self.args['trial_root'] / 'workspace/.prototype-workflow/requirement.json'
        self.assertEqual(json.loads(path.read_text()), result['binding'])
        self.assertEqual(json.loads((self.args['trial_root'] / 'trial-result.json').read_text()), result)
        self.assertEqual(dict(os.environ), before); self.assertEqual(Path.cwd(), cwd)

    def test_bad_hash_rejected_before_creating_trial_or_backend(self):
        with patch.object(manager, 'Backend') as backend:
            with self.assertRaisesRegex(ValueError, 'PACKAGE_MISMATCH'):self.start(package_sha256='0' * 64)
            backend.assert_not_called()
        self.assertFalse(self.args['trial_root'].exists())

    def test_version_mismatch_rejected_before_trial(self):
        with self.assertRaisesRegex(ValueError, 'PLUGIN_VERSION_MISMATCH'):self.start(version='0.2.5')
        self.assertFalse(self.args['trial_root'].exists())

    def test_existing_root_is_never_reused_or_modified(self):
        root = self.args['trial_root'];root.mkdir();(root / 'keep').write_text('original')
        with self.assertRaisesRegex(ValueError, 'TRIAL_ROOT_EXISTS'):self.start()
        self.assertEqual(list(root.iterdir()), [root / 'keep'])
        self.assertEqual((root / 'keep').read_text(), 'original')

    def test_trial_and_package_symlinks_rejected(self):
        alias = self.root / 'alias';alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'SYMLINK'):self.start(trial_root=alias / 'trial')
        self.archive.unlink();self.archive.symlink_to(self.user)
        with self.assertRaisesRegex(ValueError, 'SYMLINK'):self.start()

    def test_root_inside_plugin_rejected(self):
        with self.assertRaisesRegex(ValueError, 'TRIAL_INSIDE_PLUGIN'):
            self.start(trial_root=self.plugin / 'trial')

    def test_identity_rejects_wrong_permissions_unknown_field_and_key_path(self):
        self.user.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'INVALID_USER_CONFIG'):self.start()
        self.user.chmod(0o600);data=json.loads(self.user.read_text());data['extra']=1
        self.user.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'INVALID_SCHEMA'):self.start()
        with self.assertRaisesRegex(ValueError, 'INVALID_USER_CONFIG'):
            self.start(user_config=self.root / '.ssh/key.json')
        self.assertFalse(self.args['trial_root'].exists())

    def test_install_failure_leaves_no_binding_or_success(self):
        def fail(_):raise manager.c.WorkflowError('INSTALL_FAILED')
        with self.simulated_install(after_setup=fail):
            with self.assertRaisesRegex(ValueError, 'INSTALL_FAILED'):self.start()
        root=self.args['trial_root']
        self.assertFalse((root / 'workspace/.prototype-workflow/requirement.json').exists())
        self.assertFalse((root / 'trial-result.json').exists())
        self.assertEqual(json.loads((root/'trial-error.json').read_text())['error'], 'INSTALL_FAILED')

    def test_source_drift_after_install_has_no_binding(self):
        with self.simulated_install(after_setup=lambda _:self.archive.write_bytes(b'drift')):
            with self.assertRaisesRegex(ValueError, 'PACKAGE_MISMATCH'):self.start()
        root=self.args['trial_root']
        self.assertFalse((root/'workspace/.prototype-workflow/requirement.json').exists())
        self.assertFalse((root/'trial-result.json').exists())

    def test_binding_verification_failure_removes_own_binding(self):
        def fail(b,path):raise manager.c.WorkflowError('BINDING_DRIFT')
        with self.simulated_install(verify=fail):
            with self.assertRaisesRegex(ValueError, 'BINDING_DRIFT'):self.start()
        self.assertFalse((self.args['trial_root']/'workspace/.prototype-workflow/requirement.json').exists())

    def test_source_drift_after_binding_removes_binding(self):
        original=FakeBackend.verify_requirement
        def drift(b,path):
            value=original(b,path);self.archive.write_bytes(b'changed after binding');return value
        with self.simulated_install(verify=drift):
            with self.assertRaisesRegex(ValueError, 'PACKAGE_MISMATCH'):self.start()
        root=self.args['trial_root']
        self.assertFalse((root/'workspace/.prototype-workflow/requirement.json').exists())
        self.assertFalse((root/'trial-result.json').exists())

    def test_success_record_interruption_cleans_binding_and_success(self):
        original=storage.write_once
        def interrupted(path,data):
            original(path,data)
            if path.name=='trial-result.json':raise OSError('test fsync interruption')
        with self.simulated_install(),patch.object(storage,'write_once',side_effect=interrupted):
            with self.assertRaises(OSError):self.start()
        root=self.args['trial_root']
        self.assertFalse((root/'workspace/.prototype-workflow/requirement.json').exists())
        self.assertFalse((root/'trial-result.json').exists())


class SelectedSetupTests(unittest.TestCase):
    def test_setup_rechecks_expected_release_before_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve();b=FakeBackend(home)
            b.prepare_package=lambda *_:self.fail('changed candidate must not be imported')
            with self.assertRaisesRegex(ValueError,'CANDIDATE_CHANGED'):
                manager.setup(home=home,plugin_root=home,company_profile=home/'profile',ssh_user='test',
                              ssh_identity='unused',backend=b,expected_release={'different':'release'})


class DependencyCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        self.source=self.root/'installed';self.source.mkdir()
        (self.source/'state.json').write_text(json.dumps({'status':'installed','version':'0.2.2'}))
        self.lock=self.source/'release/versions/0.2.2__0.2.2/skill/prototype-design-workflow/assets/developer-reader-shell/package-lock.json'
        self.lock.parent.mkdir(parents=True);self.lock.write_bytes(b'locked dependencies')
        (self.source/'cache/npm').mkdir(parents=True);(self.source/'cache/npm/object').write_text('cached')
        (self.source/'browsers/chromium-1').mkdir(parents=True)
        (self.source/'browsers/chromium-1/INSTALLATION_COMPLETE').write_text('')
        self.package=self.root/'package';(self.package/'registry/releases').mkdir(parents=True)
        with zipfile.ZipFile(self.package/'registry/releases/enterprise-prototype-skills-0.2.6.zip','w') as z:
            z.writestr('prototype-design-workflow/assets/developer-reader-shell/package-lock.json',self.lock.read_bytes())
        self.home=self.root/'home'
    def tearDown(self):self.tmp.cleanup()
    def seed(self):return local_trial.seed_dependency_cache(self.source,self.home,{'distribution_version':'0.2.6'},self.package)
    def test_same_lock_cache_copied_and_marked_non_cold(self):
        result=self.seed();self.assertEqual(result['cold_install'],False)
        self.assertEqual((self.home/'.local/share/enterprise-prototype/0.2.6/cache/npm/object').read_text(),'cached')
    def test_mismatched_lock_has_no_cache_output(self):
        self.lock.write_text('different')
        with self.assertRaisesRegex(ValueError,'CACHE_LOCK_MISMATCH'):self.seed()
        self.assertFalse(self.home.exists())
    def test_nested_cache_escape_rejected_without_copying(self):
        (self.source/'cache/npm/escape').symlink_to(self.root)
        with self.assertRaisesRegex(ValueError,'CACHE_SYMLINK_ESCAPE'):self.seed()
        self.assertFalse(self.home.exists())
    def test_uninstalled_cache_rejected(self):
        (self.source/'state.json').write_text(json.dumps({'status':'failed','version':'0.2.2'}))
        with self.assertRaisesRegex(ValueError,'CACHE_NOT_INSTALLED'):self.seed()


if __name__=='__main__':unittest.main()
