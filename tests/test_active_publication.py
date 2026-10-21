"""UPD-01: production outer transaction, deliberately small kit collaborator."""
import contextlib,json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from plugin_runtime import manager,storage,contracts

def release(version):
    return dict(schema_version=1,distribution_version=version,plugin_name='prototype-workflow',
                marketplace_name='qqx-prototype',package_path=f'packages/{version}.zip',
                package_sha256='a'*64,skill_sha256='b'*64,pack_sha256='c'*64)

class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name).resolve();self.backend=manager.Backend(self.home)
        self.active=self.backend.data/'active.json';self.link=self.home/'default'
        self.old=release('0.2.0');self.new=release('0.2.2');self.actions=[]
        self.app=self.home/'app';self.app.mkdir();self.old_target=self.home/'old';self.new_target=self.home/'new'
        self.work=self.home/'work';self.business=self.home/'old-output';self.business.write_bytes(b'protected')
        @contextlib.contextmanager
        def runtime(_):yield object()
        def install(*_):
            self.actions.append('install');self.link.unlink(missing_ok=True);self.link.symlink_to(self.new_target)
            (self.app/'state.json').write_text('{"status":"installed"}')
        def rollback(*_):
            if not self.link.is_symlink() or self.link.readlink()!=self.new_target:raise ValueError('LINK_DRIFT')
            self.actions.append('rollback');self.link.unlink()
            if self.previous_link:self.link.symlink_to(self.old_target)
            (self.app/'state.json').write_text('{"status":"rolled_back"}')
        kit=SimpleNamespace(app_root=lambda _:self.app,runtime=runtime,install=install,
                            rollback=rollback,read_json=contracts.read)
        @contextlib.contextmanager
        def open_kit(_):yield kit
        for target,name,value in ((self.backend,'prepare_package',lambda _:self.home/'package'),
                                  (self.backend,'kit',open_kit),(self.backend,'verify_install',lambda _:None),
                                  (self.backend,'refresh',lambda:None),(self.backend,'approved',lambda:self.new)):
            p=patch.object(target,name,value);p.start();self.addCleanup(p.stop)

    def prepare(self,upgrade):
        self.previous_link=upgrade
        if upgrade:
            self.active.parent.mkdir(parents=True,exist_ok=True)
            self.active.write_text(json.dumps(self.old,indent=2)+'\n');self.active.chmod(0o640)
            self.link.symlink_to(self.old_target)
        self.before=self.active.read_bytes() if upgrade else None

    def start(self):
        return manager.start_requirement(home=self.home,workspace=self.work,
            loaded_plugin_version='0.2.2',backend=self.backend)

    def assert_restored(self):
        self.assertEqual(self.active.read_bytes() if self.active.exists() else None,self.before)
        if self.previous_link:
            self.assertEqual(self.link.readlink(),self.old_target)
            self.assertEqual(self.active.stat().st_mode&0o777,0o640)
        else:self.assertFalse(self.link.is_symlink())
        self.assertEqual(contracts.read(self.app/'state.json')['status'],'rolled_back')
        self.assertEqual(self.actions,['install','rollback'])
        self.assertEqual(self.business.read_bytes(),b'protected')
        self.assertFalse((self.work/'.prototype-workflow/requirement.json').exists())

    def failure(self,upgrade,point,error):
        self.prepare(upgrade);replace=os.replace;sync=storage._sync;fired=[]
        def replace_fault(src,dst):
            if Path(dst)==self.active and not fired:
                fired.append(True)
                if point!='before_replace':replace(src,dst)
                raise error('publication fault')
            return replace(src,dst)
        def sync_fault(path):
            if path==self.active.parent and not fired:
                fired.append(True);raise error('directory fsync fault')
            return sync(path)
        target=patch.object(storage,'_sync',side_effect=sync_fault) if point=='fsync' else patch.object(storage.os,'replace',side_effect=replace_fault)
        with target,self.assertRaises(OSError if error is TimeoutError else error):self.start()
        self.assertTrue(fired);self.assert_restored()

    def test_first_before_replace(self):self.failure(False,'before_replace',OSError)
    def test_upgrade_before_replace(self):self.failure(True,'before_replace',OSError)
    def test_first_after_replace(self):self.failure(False,'after_replace',OSError)
    def test_upgrade_after_replace(self):self.failure(True,'after_replace',OSError)
    def test_first_interrupted_after_replace(self):self.failure(False,'after_replace',KeyboardInterrupt)
    def test_upgrade_interrupted_after_replace(self):self.failure(True,'after_replace',KeyboardInterrupt)
    def test_first_directory_fsync(self):self.failure(False,'fsync',OSError)
    def test_upgrade_directory_fsync(self):self.failure(True,'fsync',OSError)
    def test_local_timeout_never_becomes_network_fallback(self):self.failure(True,'after_replace',TimeoutError)

    def test_unknown_pointer_same_content_different_inode_preserved(self):
        self.prepare(True);write=storage.write;unknown=[]
        def publish(path,document,**kwargs):
            write(path,document,**kwargs)
            if path==self.active:
                other=self.home/'foreign';other.write_bytes(self.active.read_bytes())
                os.replace(other,path);unknown.append(path.stat().st_ino);raise OSError('fault')
        with patch.object(storage,'write',side_effect=publish),self.assertRaisesRegex(ValueError,'INSTALL_RECOVERY_FAILED'):self.start()
        self.assertEqual(self.active.stat().st_ino,unknown[0]);self.assertEqual(contracts.read(self.active),self.new)
        self.assertEqual(self.link.readlink(),self.old_target)
        self.assertFalse((self.work/'.prototype-workflow/requirement.json').exists())

    def test_unknown_link_preserved_and_pointer_still_recovered(self):
        self.prepare(True);write=storage.write;foreign=self.home/'foreign'
        def publish(path,document,**kwargs):
            write(path,document,**kwargs)
            if path==self.active:
                self.link.unlink();self.link.symlink_to(foreign);raise OSError('fault')
        with patch.object(storage,'write',side_effect=publish),self.assertRaisesRegex(ValueError,'INSTALL_RECOVERY_FAILED'):self.start()
        self.assertEqual(self.link.readlink(),foreign);self.assertEqual(self.active.read_bytes(),self.before)

    def test_existing_install_not_rolled_back_on_pointer_failure(self):
        self.prepare(True);self.link.unlink();self.link.symlink_to(self.new_target)
        (self.app/'state.json').write_text('{"status":"installed"}')
        with patch.object(storage,'write',side_effect=OSError('fault')),self.assertRaises(OSError):self.backend.install(self.new)
        self.assertEqual(self.actions,[]);self.assertEqual(self.link.readlink(),self.new_target)
        self.assertEqual(self.active.read_bytes(),self.before)

    def test_pointer_changed_before_publication_is_not_overwritten(self):
        self.prepare(True);foreign=b'{"foreign":true}\n'
        def verify(_):self.active.write_bytes(foreign)
        with patch.object(self.backend,'verify_install',side_effect=verify),self.assertRaisesRegex(ValueError,'INSTALL_RECOVERY_FAILED'):self.start()
        self.assertEqual(self.active.read_bytes(),foreign);self.assertEqual(self.link.readlink(),self.old_target)

if __name__=='__main__':unittest.main()
