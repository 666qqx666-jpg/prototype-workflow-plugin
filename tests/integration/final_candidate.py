import argparse,contextlib,io,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]));sys.path.insert(0,str(Path(__file__).resolve().parent))
from plugin_runtime import manager,contracts,storage
from support import SyntheticBackend,synthetic_profile,seed,dump,ROOT

def main(output):
    output.mkdir(parents=True,exist_ok=False);home=output/'home';home.mkdir()
    backend=SyntheticBackend(home,ROOT/'plugins/prototype-workflow',output/'commands')
    release=backend.approved();package=backend.prepare_package(release);cache=seed(home,release['distribution_version']);profile,identity=synthetic_profile(home)
    failures=[];prior={}
    with backend.transport(),backend.kit(package) as kit:
        kit.company_profile.import_profile(home,profile)
        for expected in ('configuration_required','authorization_required','host_identity_blocked','service_unavailable','query_probe_blocked'):
            if expected!='configuration_required' and not kit.code_query_user_config(home).exists():kit.configure_code_query(home,'synthetic.user',str(identity))
            backend.mode=expected
            with kit.runtime(package) as api,contextlib.redirect_stdout(io.StringIO()):
                try:kit.install(package,home,api);raise AssertionError('failure installed')
                except kit.GateError as e:assert e.status==expected,(expected,e.status)
            app=kit.app_root(home)
            assert not list(app.glob('receipts/*.json')) and not (app/'START-HERE.md').exists() and not (app/'state.json').exists()
            assert not any((home/client/'skills'/name).is_symlink() for client in ('.agents','.codex','.claude','.openclaw') for name in kit.SKILLS)
            failures.append(expected);print('failure case passed: '+expected,flush=True)
        for name in kit.SKILLS[:4]:
            for client in ('.agents','.codex','.claude','.openclaw'):
                link=home/client/'skills'/name;link.parent.mkdir(parents=True,exist_ok=True);link.symlink_to(output/'synthetic-old'/name);prior[link]=os.readlink(link)
        backend.mode='authorization_required'
        with kit.runtime(package) as api,contextlib.redirect_stdout(io.StringIO()):
            try:kit.install(package,home,api);raise AssertionError('upgrade installed')
            except kit.GateError:pass
        assert all(os.readlink(p)==v for p,v in prior.items())
        backend.mode='pass';backend.install(release);backend.verify_install(release)
        app,skill,pack=kit.roots(home);activation=kit.read_json(kit.state_for(home)[1]);query=kit.read_json(app/'receipts'/activation['code_query_receipt'])
        assert activation['schema_version']==2 and query['schema_version']==3 and query['company_profile_sha256']==activation['company_profile_sha256']
        before=backend.calls
        with kit.runtime(app/'kit') as api:normal=kit.doctor(app/'kit',home,api,query=False)
        normal_commands=backend.calls-before
        before=backend.calls
        with kit.runtime(app/'kit') as api:smoke=kit.doctor(app/'kit',home,api,smoke=True,query=False)
        assert backend.calls>before+normal_commands
        workspace=output/'requirement';binding=backend.describe_install(release);backend.validate_binding(binding,workspace);storage.write_once(workspace/'.prototype-workflow/requirement.json',binding)
        backend.verify_requirement(workspace/'.prototype-workflow/requirement.json')
        # Run actual installed prototype CLI through the bound executor.
        assert backend.invoke(binding,['prototype','entry-create','--help']) in (0,None)
        from synthetic_workflow import verify_workflow
        workflow=verify_workflow(backend,binding,output/'synthetic-workflow')
        protected={p:p.read_bytes() for p in workspace.rglob('*') if p.is_file()}
        rolled=backend.rollback(release)
        assert all(os.readlink(p)==v for p,v in prior.items())
        assert all(p.read_bytes()==v for p,v in protected.items())
        backend.install(release);backend.verify_requirement(workspace/'.prototype-workflow/requirement.json')
        assert all(p.read_bytes()==v for p,v in protected.items())
        # Profile and archive drift must fail closed; restore only the synthetic test files.
        service=kit.company_profile.root(home)/'service.json';old=service.read_bytes()
        try:
            service.chmod(0o600);service.write_text('{}');service.chmod(0o400)
            try:backend.verify_install(release);raise AssertionError('profile drift accepted')
            except (ValueError,RuntimeError):pass
        finally:service.chmod(0o600);service.write_bytes(old);service.chmod(0o400)
        package_file=backend.release_record(release['distribution_version']).parent/'package.zip';old_package=package_file.read_bytes()
        try:
            package_file.write_bytes(old_package+b'x')
            try:backend.verify_install(release);raise AssertionError('archive drift accepted')
            except ValueError:pass
        finally:package_file.write_bytes(old_package)
        backend.verify_install(release)
        executor=app/'kit/prototype-kit.py';old_executor=executor.read_bytes();mode=executor.stat().st_mode&0o777
        try:
            executor.chmod(0o600);executor.write_bytes(old_executor+b'\n')
            try:backend.verify_requirement(workspace/'.prototype-workflow/requirement.json');raise AssertionError('runtime drift accepted')
            except ValueError:pass
        finally:executor.write_bytes(old_executor);executor.chmod(mode)
        backend.verify_install(release)
        result={'status':'pass','workflow':workflow,'rollback_reinstall':True,'drift_rejected':True,'synthetic':True,'business_review':False,'failures':failures,'preserves_old_16_links':True,'activation_links':20,'core':activation['smoke'],'explicit_smoke':smoke,'default_doctor':normal,'cache':cache,'query_calls':backend.query_calls}
        dump(output/'RESULT.json',result)
    print(json.dumps(result),flush=True)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();main(a.output.resolve())

def verify_interrupted_install(output):
    """Interrupt after a real link write; exercise the original recovery path."""
    from unittest.mock import patch
    output.mkdir(parents=True,exist_ok=False);home=output/'home';home.mkdir()
    backend=SyntheticBackend(home,ROOT/'plugins/prototype-workflow',output/'commands');release=backend.approved();package=backend.prepare_package(release)
    seed(home,release['distribution_version']);profile,identity=synthetic_profile(home);prior={};interrupted=[]
    with backend.transport(),backend.kit(package) as kit:
        kit.company_profile.import_profile(home,profile);kit.configure_code_query(home,'synthetic.user',str(identity))
        for name in kit.SKILLS[:4]:
            for client in ('.agents','.codex','.claude','.openclaw'):
                link=home/client/'skills'/name;link.parent.mkdir(parents=True,exist_ok=True);link.symlink_to(output/'old-entry'/name);prior[link]=os.readlink(link)
        with kit.runtime(package) as api:
            original=api.install._replace_link
            def cut_after_write(path,target):
                original(path,target)
                if not interrupted:interrupted.append(str(path));raise KeyboardInterrupt()
            with patch.object(api.install,'_replace_link',side_effect=cut_after_write):
                try:kit.install(package,home,api);raise AssertionError('interruption not raised')
                except KeyboardInterrupt:pass
        app=kit.app_root(home)
        assert interrupted and all(os.readlink(p)==target for p,target in prior.items())
        assert not list(app.glob('receipts/*.json')) and not (app/'START-HERE.md').exists() and not (app/'state.json').exists()
        assert not (backend.data/'active.json').exists()
    result={'status':'pass','interrupted_after_real_link_write':True,'old_links_restored':len(prior),'new_success_receipts':0,'active_not_published':True,'package_sha256':release['package_sha256']}
    dump(output/'RESULT.json',result);return result
