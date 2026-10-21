"""UPD-01 fault in the native-installed entry, using the real kit installer.

The source was installed separately via native Codex + loopback Git. Refresh is
held at that verified cache so this test can fault local publication only.
No install/verification/dependency/Chromium checks are replaced.
"""
import argparse,contextlib,hashlib,json,os,sys
from pathlib import Path
from unittest.mock import patch

def main(output,plugin_root,old_plugin_root):
    output.mkdir(parents=True,exist_ok=False)
    sys.path.insert(0,str(plugin_root));sys.path.insert(1,str(Path(__file__).resolve().parent))
    from plugin_runtime import manager,storage,contracts
    from support import SyntheticBackend,synthetic_profile,seed,dump
    assert Path(manager.__file__).resolve()==plugin_root/'plugin_runtime/manager.py'
    home=output/'home';home.mkdir();backend=SyntheticBackend(home,old_plugin_root,output/'commands')
    profile,identity=synthetic_profile(home);old=backend.approved();seed(home,old['distribution_version'])
    result={'status':'running','manager_path':manager.__file__,
            'manager_sha256':contracts.sha(Path(manager.__file__)),
            'storage_sha256':contracts.sha(Path(storage.__file__)),
            'kit_collaborator':'real','ssh_transport':'synthetic','refresh':'held at verified native cache',
            'real_home_install':False,'business_review':False,'faults':[]}
    dump(output/'RESULT.json',result)
    with backend.transport(),patch.object(backend,'refresh',lambda:None):
        manager.setup(home=home,plugin_root=old_plugin_root,company_profile=profile,
            ssh_user='synthetic.user',ssh_identity=str(identity),backend=backend)
        old_workspace=output/'old-requirement'
        binding=manager.start_requirement(home=home,workspace=old_workspace,
            loaded_plugin_version=old['distribution_version'],backend=backend)['binding']
        binding_path=old_workspace/'.prototype-workflow/requirement.json'
        old_bytes=binding_path.read_bytes();artifact=old_workspace/'business-result.txt';artifact.write_bytes(b'protected output')
        active=backend.data/'active.json';old_active=active.read_bytes()
        links={p:os.readlink(p) for client in ('.agents','.codex','.claude','.openclaw')
               for p in (home/client/'skills').iterdir() if p.is_symlink()}
        protected={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(binding['runtime_root']).rglob('*')
                   if p.is_file() and not p.is_symlink()}
        backend.root=plugin_root;new=backend.approved();seed(home,new['distribution_version'])
        app=home/'.local/share/enterprise-prototype'/new['distribution_version']
        # One real activation; fail AFTER pointer replacement at the durability boundary.
        sync=storage._sync;fired=[]
        def fault(path):
            if path==active.parent and not fired:
                assert contracts.read(active)==new
                assert contracts.read(app/'state.json')['status']=='installed'
                assert new['distribution_version'] in os.readlink(home/'.agents/skills/enterprise-prototype-chain')
                fired.append(True);raise KeyboardInterrupt('synthetic post-publication interruption')
            return sync(path)
        failed_workspace=output/'failed-requirement'
        with patch.object(storage,'_sync',side_effect=fault):
            try:
                manager.start_requirement(home=home,workspace=failed_workspace,
                    loaded_plugin_version=new['distribution_version'],backend=backend)
                raise AssertionError('failure was swallowed')
            except KeyboardInterrupt:pass
        assert fired and active.read_bytes()==old_active
        assert all(p.is_symlink() and os.readlink(p)==v for p,v in links.items())
        assert contracts.read(app/'state.json')['status']=='rolled_back'
        assert not (failed_workspace/'.prototype-workflow/requirement.json').exists()
        assert manager.resume_requirement(home=home,workspace=old_workspace,backend=backend)['binding']==binding
        assert backend.invoke(binding,['prototype','entry-create','--help']) in (0,None)
        result['faults'].append({'point':'active replaced, directory fsync','exception':'KeyboardInterrupt',
            'restored_links':len(links),'old_active_exact':True,'new_state':'rolled_back','new_binding_absent':True})
        dump(output/'RESULT.json',result);print('real post-publication interruption restored old installation',flush=True)
        # Retry -> reload -> new binding; old binding must still invoke its own entities.
        retry_workspace=output/'new-requirement'
        reload=manager.start_requirement(home=home,workspace=retry_workspace,
            loaded_plugin_version=old['distribution_version'],backend=backend)
        assert reload['status']=='reload_required'
        assert not (retry_workspace/'.prototype-workflow/requirement.json').exists()
        updated=manager.start_requirement(home=home,workspace=retry_workspace,
            loaded_plugin_version=new['distribution_version'],backend=backend)
        assert updated['status']=='ready' and contracts.read(active)==new
        assert manager.resume_requirement(home=home,workspace=old_workspace,backend=backend)['binding']==binding
        assert updated['binding']['runtime_root']!=binding['runtime_root']
        assert backend.invoke(binding,['prototype','entry-create','--help']) in (0,None)
        backend.rollback(new)
        assert active.read_bytes()==old_active and all(os.readlink(p)==v for p,v in links.items())
        backend.install(new);backend.verify_install(new)
        assert manager.resume_requirement(home=home,workspace=old_workspace,backend=backend)['binding']==binding
        assert binding_path.read_bytes()==old_bytes and artifact.read_bytes()==b'protected output'
        assert all(hashlib.sha256(p.read_bytes()).hexdigest()==v for p,v in protected.items())
        assert manager.resume_requirement(home=home,workspace=retry_workspace,backend=backend)['binding']==updated['binding']
        result.update(status='pass',old_release=old,new_release=new,old_binding_unchanged=True,
            old_runtime_file_count=len(protected),old_runtime_bytes_unchanged=True,
            retry_reload_and_new_binding=True,rollback_reinstall=True,query_calls=backend.query_calls)
        dump(output/'RESULT.json',result)
    print('UPD-01 native entry / real installer / retry / rollback / old binding passed',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--plugin-root',type=Path,required=True);p.add_argument('--old-plugin-root',type=Path,required=True)
    a=p.parse_args();main(a.output.resolve(),a.plugin_root.resolve(),a.old_plugin_root.resolve())
