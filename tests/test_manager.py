import tempfile,unittest,uuid
from pathlib import Path
from plugin_runtime.manager import start_requirement,resume_requirement
from plugin_runtime import storage,contracts
class FakeBackend:
    def __init__(self,home):self.home=home;self.current='0.2.0';self.refresh_calls=0;self.fail=None;self.installed={};self.active=None;self.publish(self.current)
    update_lock=staticmethod(storage.update_lock)
    def publish(self,v):
        self.current=v;root=self.home/'runtime'/v;root.mkdir(parents=True,exist_ok=True)
        for f in ['executor','skill','pack','manifest','START-HERE']: (root/f).write_text(v+f)
    def activate_default(self,v):self.active=v
    def refresh(self):
        self.refresh_calls+=1
        if self.fail:raise self.fail
    def approved(self):return {'distribution_version':self.current,'package_sha256':contracts.sha(self.home/'runtime'/self.current/'executor')}
    def install(self,r):self.installed[r['distribution_version']]=r;self.active=r['distribution_version']
    def verify_install(self,r):
        if contracts.sha(self.home/'runtime'/r['distribution_version']/'executor')!=r['package_sha256']:raise ValueError('DRIFT')
    def previous_usable_release(self,home):return self.installed.get(self.active)
    def notice(self,*a):pass
    def plugin_path(self,r):return self.home/'plugin'/r['distribution_version']
    def describe_install(self,r):
        root=self.home/'runtime'/r['distribution_version'];return {'version':r['distribution_version'],'distribution_version':r['distribution_version'],'runtime_root':str(root),'executor_sha256':contracts.sha(root/'executor'),'requirement_id':str(uuid.uuid4())}
    def validate_binding(self,b,w):pass
    def verify_requirement(self,p):
        b=contracts.read(p)
        if contracts.sha(Path(b['runtime_root'])/'executor')!=b['executor_sha256']:raise ValueError('DRIFT')
        return b
    def remove_own_binding(self,p,b):
        if p.read_bytes()==storage.payload(b):p.unlink()
class ManagerTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name).resolve();self.work=self.home/'work';self.backend=FakeBackend(self.home)
    def tearDown(self):self.tmp.cleanup()
    def start(self,loaded='0.2.0'):return start_requirement(home=self.home,workspace=self.work,loaded_plugin_version=loaded,backend=self.backend)
    def test_resume_does_not_refresh_or_follow_default(self):
        original=self.start();self.backend.publish('0.2.1');self.backend.activate_default('0.2.1');self.backend.refresh_calls=0
        b=resume_requirement(home=self.home,workspace=self.work,backend=self.backend)['binding']
        self.assertEqual(b,original['binding']);self.assertEqual(self.backend.refresh_calls,0)
    def test_reload_after_install(self):
        self.backend.publish('0.2.1');self.assertEqual(self.start()['status'],'reload_required');self.assertIn('0.2.1',self.backend.installed);self.assertFalse((self.work/'.prototype-workflow/requirement.json').exists())
    def test_no_replace(self):
        self.start()
        with self.assertRaisesRegex(ValueError,'REQUIREMENT_EXISTS'):self.start()
    def test_network_fallback(self):
        self.start();self.work=self.home/'next';self.backend.fail=ConnectionError();self.assertEqual(self.start('0.2.1')['update'],'fallback')
    def test_no_fallback_on_integrity(self):
        self.backend.fail=ValueError('INTEGRITY')
        with self.assertRaisesRegex(ValueError,'INTEGRITY'):self.start()
    def test_first_install_network_failure(self):
        self.backend.fail=ConnectionError()
        with self.assertRaisesRegex(ValueError,'UPDATE_UNAVAILABLE'):self.start()
    def test_drift_blocks_resume(self):
        b=self.start()['binding'];(Path(b['runtime_root'])/'executor').write_text('changed')
        with self.assertRaisesRegex(ValueError,'DRIFT'):resume_requirement(home=self.home,workspace=self.work,backend=self.backend)

class AtomicityTests(unittest.TestCase):
    def test_two_processes_only_one_requirement(self):
        import multiprocessing
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve();backend=FakeBackend(home);work=home/'work';ctx=multiprocessing.get_context('fork');queue=ctx.Queue()
            def child():
                try:queue.put(start_requirement(home=home,workspace=work,loaded_plugin_version='0.2.0',backend=backend)['status'])
                except ValueError as e:queue.put(str(e))
            ps=[ctx.Process(target=child) for _ in range(2)]
            for p in ps:p.start()
            for p in ps:p.join(10);self.assertEqual(p.exitcode,0)
            self.assertCountEqual([queue.get(timeout=1),queue.get(timeout=1)],['ready','REQUIREMENT_EXISTS'])
    def test_symlink_parent_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();real=root/'real';real.mkdir();alias=root/'alias';alias.symlink_to(real)
            with self.assertRaisesRegex(ValueError,'SYMLINK'):storage.write_once(alias/'binding.json',{})
    def test_update_and_resume_parallel(self):
        import multiprocessing
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve();backend=FakeBackend(home);work=home/'old'
            first=start_requirement(home=home,workspace=work,loaded_plugin_version='0.2.0',backend=backend);backend.publish('0.2.1')
            ctx=multiprocessing.get_context('fork');queue=ctx.Queue()
            def update():queue.put(start_requirement(home=home,workspace=home/'new',loaded_plugin_version='0.2.1',backend=backend)['binding']['distribution_version'])
            p=ctx.Process(target=update);p.start()
            self.assertEqual(resume_requirement(home=home,workspace=work,backend=backend)['binding'],first['binding'])
            p.join(10);self.assertEqual(p.exitcode,0);self.assertEqual(queue.get(timeout=1),'0.2.1')

class NativeExitTests(unittest.TestCase):
    def test_native_help_exit_is_returned_not_propagated(self):
        from unittest.mock import patch
        from contextlib import contextmanager
        from types import SimpleNamespace
        from plugin_runtime.manager import Backend
        with tempfile.TemporaryDirectory() as tmp:
            backend=Backend(Path(tmp).resolve())
            @contextmanager
            def runtime(_):yield object()
            def dispatch(*args):raise SystemExit(0)
            @contextmanager
            def kit(_):yield SimpleNamespace(runtime=runtime,dispatch_bound=dispatch)
            with patch.object(backend,'validate_binding'),patch.object(backend,'kit',kit):self.assertEqual(backend.invoke({'runtime_root':tmp},['prototype','--help']),0)

class ProductionBoundaryTests(unittest.TestCase):
    def test_cached_package_tree_drift_rejected_before_import(self):
        from plugin_runtime.manager import Backend
        with tempfile.TemporaryDirectory() as tmp:
            backend=Backend(Path(tmp).resolve(),Path('plugins/prototype-workflow').resolve());release=backend.approved();package=backend.prepare_package(release)
            executor=package/'prototype-kit.py';executor.write_bytes(executor.read_bytes()+b'\n# drift\n')
            with self.assertRaisesRegex(ValueError,'PACKAGE_TREE_DRIFT'):backend.prepare_package(release)
    def test_same_version_content_replacement_rejected(self):
        from plugin_runtime.manager import Backend
        with tempfile.TemporaryDirectory() as tmp:
            backend=Backend(Path(tmp).resolve(),Path('plugins/prototype-workflow').resolve());release=backend.approved();backend.prepare_package(release)
            changed=dict(release,skill_sha256='f'*64)
            with self.assertRaisesRegex(ValueError,'IMMUTABLE_VERSION_MISMATCH'):backend.prepare_package(changed)
    def test_closed_schema_rejects_boolean_and_unknown_field(self):
        from plugin_runtime import contracts as c
        d=c.read(Path('release/approved.json'))
        for changed in (dict(d,schema_version=True),dict(d,unknown=1),dict(d,package_path='../outside.zip')):
            with self.assertRaises(ValueError):c.release(changed)
