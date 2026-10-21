"""Deterministic update and requirement pinning. Only network failures may fall back."""
import contextlib,importlib.util,json,os,shutil,subprocess,sys,tempfile,uuid,zipfile
from pathlib import Path
from . import contracts as c, storage

SOURCE='https://github.com/666qqx666-jpg/prototype-workflow-plugin'
MARKET='qqx-prototype'
PLUGIN='prototype-workflow'

def start_requirement(*,home,workspace,loaded_plugin_version,backend):
    home=Path(home).resolve();workspace=Path(workspace).absolute();storage.no_symlink(workspace);workspace=workspace.resolve();c.version(loaded_plugin_version)
    path=workspace/'.prototype-workflow/requirement.json';storage.no_symlink(path)
    if path.exists():raise c.WorkflowError('REQUIREMENT_EXISTS')
    with backend.update_lock(home):
        fallback=False
        try:
            backend.refresh();release=backend.approved();backend.install(release);backend.verify_install(release)
        except (TimeoutError,ConnectionError):
            release=backend.previous_usable_release(home)
            if release is None:raise c.WorkflowError('UPDATE_UNAVAILABLE') from None
            backend.verify_install(release);fallback=True;backend.notice('using_previous_version',release['distribution_version'])
        if not fallback and loaded_plugin_version!=release['distribution_version']:
            return {'status':'reload_required','distribution_version':release['distribution_version'],'plugin_path':str(backend.plugin_path(release))}
        binding=backend.describe_install(release);backend.validate_binding(binding,workspace)
        try:storage.write_once(path,binding)
        except FileExistsError:raise c.WorkflowError('REQUIREMENT_EXISTS') from None
        try:backend.verify_requirement(path)
        except BaseException:
            backend.remove_own_binding(path,binding);raise
        return {'status':'ready','update':'fallback' if fallback else 'current','binding':binding}

def resume_requirement(*,home,workspace,backend):
    return {'status':'ready','binding':backend.verify_requirement(Path(workspace).absolute()/'.prototype-workflow/requirement.json')}

def run_requirement(*,home,workspace,arguments,backend):
    binding=resume_requirement(home=home,workspace=workspace,backend=backend)['binding']
    if not arguments or arguments[0]!='prototype':raise c.WorkflowError('PROTOTYPE_ONLY')
    return backend.invoke(binding,arguments)

def setup(*,home,plugin_root,company_profile,ssh_user,ssh_identity,backend):
    with backend.update_lock(home):
        release=backend.approved()
        package=backend.prepare_package(release)
        with backend.kit(package) as kit:
            kit.company_profile.import_profile(home,company_profile)
            kit.configure_code_query(home,ssh_user,ssh_identity)
        backend.install(release);backend.verify_install(release)
    return {'status':'ready','distribution_version':release['distribution_version']}

class Backend:
    """Production backend. Tests inject a subclass in Python, never a CLI switch."""
    source=SOURCE
    def __init__(self,home,plugin_root=None):
        self.home=Path(home).resolve();self.root=Path(plugin_root).resolve() if plugin_root else None
        self.data=self.home/'.local/share/prototype-workflow'
        storage.no_symlink(self.home/'.codex');(self.home/'.codex').mkdir(mode=0o700,parents=True,exist_ok=True)
    update_lock=staticmethod(storage.update_lock)
    def environment(self):
        env={k:os.environ[k] for k in ('PATH','TMPDIR','LANG','SSH_AUTH_SOCK') if k in os.environ}
        env.update(HOME=str(self.home),CODEX_HOME=str(self.home/'.codex'),XDG_CONFIG_HOME=str(self.home/'.config'),GIT_TERMINAL_PROMPT='0',PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1')
        return env
    def command(self,args,timeout=45):
        try:return subprocess.run(args,cwd=self.home,env=self.environment(),capture_output=True,text=True,timeout=timeout)
        except subprocess.TimeoutExpired:raise TimeoutError('COMMAND_TIMEOUT') from None
    def codex(self,*args):
        for attempt in range(2):
            try:
                r=self.command(['codex','--no-daemon','plugin',*args,'--json'])
                if r.returncode:
                    if any(s in r.stderr.lower() for s in ('could not resolve','failed to connect','timed out','network','connection refused')):raise ConnectionError('SOURCE_UNAVAILABLE')
                    raise c.WorkflowError('NATIVE_PLUGIN_FAILED')
                return json.loads(r.stdout,object_pairs_hook=c.unique)
            except (ConnectionError,TimeoutError):
                if attempt:raise
        raise AssertionError()
    def refresh(self):
        self.codex('marketplace','upgrade',MARKET)
        installed=self.codex('add',PLUGIN+'@'+MARKET)
        if not isinstance(installed,dict) or installed.get('pluginId')!=PLUGIN+'@'+MARKET or not isinstance(installed.get('installedPath'),str):raise c.WorkflowError('NATIVE_PLUGIN_INCOMPATIBLE')
        root=Path(installed['installedPath']);storage.no_symlink(root)
        if not root.is_relative_to(self.home/'.codex/plugins/cache'):raise c.WorkflowError('INVALID_NATIVE_PATH')
        listed=self.codex('list')
        items=[i for i in listed.get('installed',[]) if i.get('pluginId')==PLUGIN+'@'+MARKET]
        if len(items)!=1 or items[0].get('marketplaceSource',{}).get('sourceType')!='git' or items[0]['marketplaceSource'].get('source','').removesuffix('.git')!=self.source.removesuffix('.git'):raise c.WorkflowError('UNTRUSTED_SOURCE')
        source_path=Path(items[0].get('source',{}).get('path',''))
        if not source_path.is_absolute() or not source_path.is_relative_to(self.home/'.codex'):raise c.WorkflowError('INVALID_NATIVE_PATH')
        branch=self.command(['git','-C',str(source_path),'symbolic-ref','--short','HEAD'])
        if branch.returncode or branch.stdout.strip()!='main':raise c.WorkflowError('UNTRUSTED_BRANCH')
        origin=self.command(['git','-C',str(source_path),'remote','get-url','origin'])
        if origin.returncode or origin.stdout.strip().removesuffix('.git')!=self.source.removesuffix('.git'):raise c.WorkflowError('UNTRUSTED_SOURCE')
        self.root=root.resolve()
    def approved(self):
        if self.root is None:raise c.WorkflowError('CONFIGURATION_REQUIRED')
        storage.no_symlink(self.root)
        release=c.release(c.read(self.root/'release/approved.json'));manifest=c.read(self.root/'plugin.json')
        if manifest.get('name')!=PLUGIN or manifest.get('version')!=release['distribution_version']:raise c.WorkflowError('PLUGIN_VERSION_MISMATCH')
        archive=self.root/release['package_path'];storage.no_symlink(archive)
        if not archive.is_file() or c.sha(archive)!=release['package_sha256']:raise c.WorkflowError('PACKAGE_MISMATCH')
        old=self.release_record(release['distribution_version'])
        if old.exists() and c.read(old)!=release:raise c.WorkflowError('IMMUTABLE_VERSION_MISMATCH')
        active=self.data/'active.json'
        if active.exists() and c.version(c.release(c.read(active))['distribution_version'])>c.version(release['distribution_version']):raise c.WorkflowError('APPROVED_VERSION_REGRESSION')
        return release
    def release_record(self,v):c.version(v);return self.data/'releases'/v/'release.json'
    def prepare_package(self,release):
        c.release(release);directory=self.release_record(release['distribution_version']).parent;storage.no_symlink(directory)
        if directory.exists():
            if c.read(directory/'release.json')!=release or c.sha(directory/'package.zip')!=release['package_sha256']:raise c.WorkflowError('IMMUTABLE_VERSION_MISMATCH')
            self.verify_package_tree(directory/'package.zip',directory/'kit')
            return directory/'kit'
        directory.parent.mkdir(parents=True,exist_ok=True)
        staged=Path(tempfile.mkdtemp(prefix='.release-',dir=directory.parent))
        try:
            shutil.copyfile(self.root/release['package_path'],staged/'package.zip')
            if c.sha(staged/'package.zip')!=release['package_sha256']:raise c.WorkflowError('PACKAGE_MISMATCH')
            with zipfile.ZipFile(staged/'package.zip') as z:
                for item in c.archive_names(z):z.extract(item,staged/'kit')
            kit=staged/'kit'
            if c.sha(kit/f"registry/releases/enterprise-prototype-skills-{release['distribution_version']}.zip")!=release['skill_sha256'] or c.sha(kit/f"registry/releases/company-prototype-{release['distribution_version']}.zip")!=release['pack_sha256']:raise c.WorkflowError('INNER_ARCHIVE_MISMATCH')
            self.verify_package_tree(staged/'package.zip',kit)
            with self.kit(kit) as module:module.verify_kit(kit)
            storage.write_once(staged/'release.json',release);os.rename(staged,directory)
        finally:
            if staged.exists():shutil.rmtree(staged)
        return directory/'kit'
    def verify_package_tree(self,archive,root):
        storage.no_symlink(root)
        with zipfile.ZipFile(archive) as z:
            items=list(c.archive_names(z));expected={i.filename for i in items if not i.is_dir()}
            actual=set()
            for p in root.rglob('*'):
                if p.is_symlink():raise c.WorkflowError('PACKAGE_TREE_DRIFT')
                if p.is_file():actual.add(str(p.relative_to(root)))
            if actual!=expected:raise c.WorkflowError('PACKAGE_TREE_DRIFT')
            for i in items:
                if not i.is_dir() and (root/i.filename).read_bytes()!=z.read(i):raise c.WorkflowError('PACKAGE_TREE_DRIFT')
    @contextlib.contextmanager
    def kit(self,package):
        old=list(sys.path);previous=sys.modules.pop('company_profile',None)
        try:
            spec=importlib.util.spec_from_file_location('bound_prototype_kit',package/'prototype-kit.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);yield module
        finally:
            sys.path[:]=old;sys.modules.pop('company_profile',None)
            if previous is not None:sys.modules['company_profile']=previous
    def install(self,release):
        package=self.prepare_package(release)
        predecessor=self.data/'predecessors'/release['distribution_version']/'release.json'
        active=self.data/'active.json'
        publication=storage.Replacement(active)
        if active.exists() and c.read(active)!=release and not predecessor.exists():
            storage.write_once(predecessor,c.release(c.read(active)))
        with self.kit(package) as kit:
            app=kit.app_root(self.home)
            already=(app/'state.json').exists() and kit.read_json(app/'state.json').get('status')=='installed'
            activated=False
            try:
                if not already:
                    with kit.runtime(package) as api:
                        kit.install(package,self.home,api)
                        activated=True
                self.verify_install(release)
                try:storage.write(active,release,replace=True,transaction=publication)
                except (TimeoutError,ConnectionError) as error:
                    # Local filesystem errors are not update-network fallback.
                    raise OSError('ACTIVE_PUBLICATION_FAILED') from error
            except BaseException:
                failures=[]
                try:
                    if activated:
                        with kit.runtime(package) as api:kit.rollback(package,self.home,api)
                except BaseException as error:failures.append(error)
                # Compensate the pointer even if link restoration itself failed;
                # both operations refuse targets not owned by this transaction.
                try:publication.restore()
                except BaseException as error:failures.append(error)
                if failures:raise c.WorkflowError('INSTALL_RECOVERY_FAILED') from failures[0]
                raise
    def rollback(self,release):
        self.verify_install(release)
        package=self.home/'.local/share/enterprise-prototype'/release['distribution_version']/'kit'
        predecessor=self.data/'predecessors'/release['distribution_version']/'release.json'
        previous=c.release(c.read(predecessor)) if predecessor.exists() else None
        if previous:self.verify_install(previous)
        with self.kit(package) as kit,kit.runtime(package) as api:result=kit.rollback(package,self.home,api)
        active=self.data/'active.json'
        if active.exists() and c.read(active)==release:
            if previous:storage.write(active,previous,replace=True)
            else:active.unlink()
        return result
    def verify_install(self,release):
        c.release(release);directory=self.release_record(release['distribution_version']).parent
        if c.read(directory/'release.json')!=release or c.sha(directory/'package.zip')!=release['package_sha256']:raise c.WorkflowError('PACKAGE_MISMATCH')
        root=self.home/'.local/share/enterprise-prototype'/release['distribution_version'];storage.no_symlink(root)
        # Verify installed kit against its immutable package, including the verifier itself, before importing.
        with zipfile.ZipFile(directory/'package.zip') as z:
            items=list(c.archive_names(z));expected={i.filename for i in items if not i.is_dir()}
            actual={str(f.relative_to(root/'kit')) for f in (root/'kit').rglob('*') if f.is_file()}
            if actual!=expected:raise c.WorkflowError('INSTALLED_KIT_DRIFT')
            for i in items:
                if not i.is_dir() and (root/'kit'/i.filename).read_bytes()!=z.read(i):raise c.WorkflowError('INSTALLED_KIT_DRIFT')
        with self.kit(root/'kit') as kit,kit.runtime(root/'kit') as api:kit.doctor(root/'kit',self.home,api,query=False,_bound=True)
    def describe_install(self,release):
        self.verify_install(release);v=release['distribution_version'];root=self.home/'.local/share/enterprise-prototype'/v;pair=root/f'release/versions/{v}__{v}'
        # Content digests are the verified archive identities; archive-to-tree checks cover actual files.
        return {'schema_version':1,'requirement_id':str(uuid.uuid4()),'distribution_version':v,'package_sha256':release['package_sha256'],'runtime_root':str(root),'executor_sha256':c.sha(root/'kit/prototype-kit.py'),'skill_root':str((pair/'skill').resolve()),'skill_content_sha256':release['skill_sha256'],'pack_root':str((pair/'pack').resolve()),'pack_content_sha256':release['pack_sha256'],'manifest_path':str(root/'kit/registry/prototype-canary-manifest.yaml'),'manifest_sha256':c.sha(root/'kit/registry/prototype-canary-manifest.yaml'),'start_here_path':str(root/'START-HERE.md'),'start_here_sha256':c.sha(root/'START-HERE.md')}
    def validate_binding(self,binding,workspace):
        c.binding(binding);storage.no_symlink(workspace)
        if workspace.is_relative_to(self.home/'.local/share') or workspace.is_relative_to(self.home/'.codex/plugins'):raise c.WorkflowError('WORKSPACE_INSIDE_INSTALL')
        expected=self.describe_install(c.release(c.read(self.release_record(binding['distribution_version']))));expected['requirement_id']=binding['requirement_id']
        if expected!=binding:raise c.WorkflowError('BINDING_DRIFT')
    def verify_requirement(self,path):
        storage.no_symlink(path);binding=c.binding(c.read(path));self.validate_binding(binding,path.parent.parent);return binding
    def remove_own_binding(self,path,binding):
        storage.no_symlink(path)
        if path.is_file() and path.read_bytes()==storage.payload(binding):path.unlink()
    def previous_usable_release(self,home):
        path=self.data/'active.json'
        if not path.exists():return None
        release=c.release(c.read(path));self.verify_install(release);return release
    def plugin_path(self,release):return self.root
    def notice(self,code,version):print(json.dumps({'notice':code,'distribution_version':version}),file=sys.stderr)
    def invoke(self,binding,arguments):
        self.validate_binding(binding,self.home/'binding-check-workspace')
        package=Path(binding['runtime_root'])/'kit'
        with self.kit(package) as kit,kit.runtime(package) as api:
            try:
                return kit.dispatch_bound(binding,arguments,lambda b:(self.validate_binding(b,self.home/'binding-check-workspace') or b),lambda root,args:kit.run_command(Path(root)/'kit',self.home,api,args,_bound=True))
            except SystemExit as error:
                return error.code if isinstance(error.code,int) else 1
            except Exception as error:
                # Match the bound bootstrap's failure exit without exposing raw native/SSH output.
                status=getattr(error,'status','restart_required')
                print(json.dumps({'status':status,'error':getattr(error,'code',getattr(error,'error',type(error).__name__)),'next_step':'保留本需求记录，按绑定版本排查；不要绕过门禁'},ensure_ascii=False),file=sys.stderr)
                return (4 if status=='configuration_required' else 5) if hasattr(error,'status') else 6
