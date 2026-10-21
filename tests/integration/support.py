"""Synthetic identity + SSH transport only; dependencies, Chromium and CLI are real."""
import base64,contextlib,hashlib,json,os,pwd,shutil,subprocess
from pathlib import Path
from unittest.mock import patch
from plugin_runtime.manager import Backend
ROOT=Path(__file__).resolve().parents[2]
OWNER=Path(pwd.getpwuid(os.getuid()).pw_dir)
REAL_RUN=subprocess.run

def dump(p,data):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def synthetic_profile(home):
    blob=b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20'+b'k'*32
    hosts='192.0.2.10 ssh-ed25519 '+base64.b64encode(blob).decode()+'\n'
    data={'schema_version':1,'service':{'schema_version':2,'host':'192.0.2.10','port':22,'remote_script':'/srv/query.js','known_hosts_sha256':hashlib.sha256(hosts.encode()).hexdigest()},'known_hosts':hosts}
    source=home/'synthetic-profile.json';dump(source,data)
    identity=home/'.ssh/synthetic-key';identity.parent.mkdir(mode=0o700,exist_ok=True);identity.write_text('synthetic, not a key');identity.chmod(0o600)
    return source,identity

def seed(home,version):
    app=home/'.local/share/enterprise-prototype'/version
    lock=json.loads((ROOT/'skills/prototype-design-workflow/assets/developer-reader-shell/package-lock.json').read_text());count=0
    for value in lock['packages'].values():
        integrity=value.get('integrity','')
        if not integrity.startswith('sha512-'):continue
        hx=base64.b64decode(integrity[7:]).hex();relative=Path('content-v2/sha512')/hx[:2]/hx[2:4]/hx[4:]
        source=OWNER/'.npm/_cacache'/relative
        if source.is_file():
            target=app/'cache/npm/_cacache'/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target);count+=1
    browsers=[]
    for source in (OWNER/'Library/Caches/ms-playwright').iterdir():
        if source.name.startswith(('chromium-','chromium_headless_shell-','ffmpeg-')) and (source/'INSTALLATION_COMPLETE').is_file():
            target=app/'browsers'/source.name
            if not target.exists():shutil.copytree(source,target,symlinks=True)
            browsers.append(source.name)
    return {'npm_content_objects':count,'browser_cache_directories':browsers,'cold_install':False}

class SyntheticBackend(Backend):
    def __init__(self,home,root,logs):super().__init__(home,root);self.logs=logs;self.mode='pass';self.calls=0;self.query_calls=0
    @contextlib.contextmanager
    def kit(self,package):
        with super().kit(package) as kit:
            owner=type('Owner',(),{'pw_dir':str(self.home)})()
            with patch.object(kit.pwd,'getpwuid',return_value=owner):yield kit
    @contextlib.contextmanager
    def transport(self):
        def run(args,**kw):
            self.calls+=1;label=f'{self.calls:04}'
            if isinstance(args,list) and args[-1:]==['doctor'] and any('code_query.js' in str(x) for x in args):
                self.query_calls+=1
                env=dict(os.environ,PROTOTYPE_SYNTHETIC_OWNER_HOME=str(self.home),PROTOTYPE_SYNTHETIC_MODE=self.mode)
                args=[args[0],'--require',str(ROOT/'tests/integration/synthetic_ssh.cjs'),*args[1:]];kw={**kw,'env':env}
            result=REAL_RUN(args,**kw)
            dump(self.logs/(label+'.json'),{'argv':list(map(str,args)),'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
            return result
        env={'HOME':str(self.home),'npm_config_userconfig':'/dev/null','npm_config_audit':'false','npm_config_fund':'false','npm_config_update_notifier':'false','npm_config_offline':'true'}
        with patch.object(subprocess,'run',side_effect=run),patch.dict(os.environ,env):yield
