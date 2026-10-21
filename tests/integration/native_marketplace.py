import argparse,contextlib,functools,http.server,json,os,shutil,subprocess,sys,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]));sys.path.insert(0,str(Path(__file__).resolve().parent))
from tools.build_public_plugin import build
from plugin_runtime import manager,contracts
from support import SyntheticBackend,synthetic_profile,seed,dump,ROOT

def main(output):
    output.mkdir(parents=True,exist_ok=False);home=output/'home';home.mkdir();source=output/'source';source.mkdir()
    backend=SyntheticBackend(home,None,output/'commands');server=None
    result={'status':'running','synthetic':True,'desktop_live_install':'not_run','final_github_source_install':'not_run','public_upload':False,'real_home_install':False}
    def git(*args):
        p=subprocess.run(['git','-c','user.name=Local fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false',*args],cwd=source,capture_output=True,text=True,timeout=45);assert p.returncode==0,p.stderr;return p.stdout
    def publish(tree,label):
        for name in ('plugins','.agents'):
            dest=source/name
            if dest.exists():shutil.rmtree(dest)
            shutil.copytree(tree/name,dest)
        git('add','.');git('commit','-m',label);git('update-server-info')
    git('init','--initial-branch=main');publish(ROOT,'local candidate 0.2.0')
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self,*a):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(source/'.git')))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();backend.source=f'http://127.0.0.1:{server.server_port}'
    try:
        added=backend.codex('marketplace','add',backend.source,'--ref','main');dump(output/'marketplace.json',added)
        backend.refresh();v1=backend.approved();profile,identity=synthetic_profile(home);seed(home,'0.2.0')
        with backend.transport():
            ready=manager.setup(home=home,plugin_root=backend.root,company_profile=profile,ssh_user='synthetic.user',ssh_identity=str(identity),backend=backend)
            a=manager.start_requirement(home=home,workspace=output/'old-requirement',loaded_plugin_version='0.2.0',backend=backend)
        bpath=output/'old-requirement/.prototype-workflow/requirement.json';original=bpath.read_bytes();assert ready['status']=='ready'
        build('0.2.1',output/'version-two');publish(output/'version-two','synthetic update only');seed(home,'0.2.1')
        with backend.transport():
            reload=manager.start_requirement(home=home,workspace=output/'new-requirement',loaded_plugin_version='0.2.0',backend=backend)
            assert reload['status']=='reload_required' and not (output/'new-requirement/.prototype-workflow/requirement.json').exists()
            b=manager.start_requirement(home=home,workspace=output/'new-requirement',loaded_plugin_version='0.2.1',backend=backend)
            resumed=manager.resume_requirement(home=home,workspace=output/'old-requirement',backend=backend)
            assert resumed['binding']==a['binding'] and bpath.read_bytes()==original
            assert all(a['binding'][k]!=b['binding'][k] for k in ('executor_sha256','skill_content_sha256','pack_content_sha256','start_here_sha256'))
            assert backend.invoke(a['binding'],['prototype','entry-create','--help']) in (0,None)
            # Default links now point to 0.2.1; the old own entities still validate.
            assert '0.2.1' in os.readlink(home/'.agents/skills/enterprise-prototype-chain')
            backend.rollback(backend.approved())
            assert contracts.read(backend.data/'active.json')['distribution_version']=='0.2.0'
            assert '0.2.0' in os.readlink(home/'.agents/skills/enterprise-prototype-chain')
            assert bpath.read_bytes()==original
            backend.install(backend.approved())
            server.shutdown();server.server_close();thread.join();server=None
            fallback=manager.start_requirement(home=home,workspace=output/'offline-requirement',loaded_plugin_version='0.2.0',backend=backend)
            assert fallback['update']=='fallback' and fallback['binding']['distribution_version']=='0.2.1'
        result.update(status='pass',v1=v1,initial=a,updated=b,resumed=resumed,reload=reload,fallback=fallback,old_binding_unchanged=True,real_execution_entities_differ=True,query_calls=backend.query_calls)
        print('native marketplace/update/binding passed',flush=True)
    finally:
        if server:server.shutdown();server.server_close();thread.join()
        result['loopback_server_stopped']=not thread.is_alive();dump(output/'RESULT.json',result)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args();main(a.output.resolve())

def verify_entry_only(output):
    """Capture final native entry bytes before a later upgrade removes its cache."""
    output.mkdir(parents=True,exist_ok=False);home=output/'home';home.mkdir();source=output/'source';source.mkdir()
    backend=SyntheticBackend(home,None,output/'commands')
    for name in ('plugins','.agents'):shutil.copytree(ROOT/name,source/name)
    for args in [('init','--initial-branch=main'),('add','.'),('commit','-m','final entry bytes'),('update-server-info',)]:
        p=subprocess.run(['git','-c','user.name=Local fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false',*args],cwd=source,capture_output=True,text=True,timeout=45);assert p.returncode==0,p.stderr
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self,*a):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(source/'.git')))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();backend.source=f'http://127.0.0.1:{server.server_port}'
    try:
        backend.codex('marketplace','add',backend.source,'--ref','main');backend.refresh();release=backend.approved()
        inventory={}
        for f in (ROOT/'plugins/prototype-workflow').rglob('*'):
            if f.is_file():
                rel=f.relative_to(ROOT/'plugins/prototype-workflow');assert f.read_bytes()==(backend.root/rel).read_bytes();inventory[str(rel)]=contracts.sha(f)
        result={'status':'pass','release':release,'entry_files':inventory,'installed_path':str(backend.root),'source':'loopback Git main','setup_claimed':False}
    finally:server.shutdown();server.server_close();thread.join()
    result['loopback_server_stopped']=not thread.is_alive();dump(output/'RESULT.json',result);return result
