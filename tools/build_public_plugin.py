"""Deterministic, exclusive public release builder. No private baseline inputs."""
import argparse,hashlib,json,re,shutil,sys,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.audit_publication import audit_tree
ROOT=Path(__file__).resolve().parents[1]
CORE=('prototype-design-workflow','enterprise-prototype-chain','enterprise-prototype-builder','enterprise-prototype-review','enterprise-prototype-code-query')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,d):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
def files(root):return sorted(p for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
def zip_tree(path,entries):
    path.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(path,'x') as z:
        for name,data in sorted(entries):
            info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16;z.writestr(info,data)
def build(version,output):
    if not re.fullmatch(r'0\.2\.[0-9]+',version):raise ValueError('INVALID_RELEASE_VERSION')
    output.mkdir(parents=True,exist_ok=False);kit=output/'kit';kit.mkdir();release=kit/'registry/releases'
    entries=[]
    for name in CORE:
        for f in files(ROOT/'skills'/name):entries.append((str(f.relative_to(ROOT/'skills')),f.read_bytes()))
    for f in files(ROOT/'runtime'):
        data=f.read_bytes()
        if str(f.relative_to(ROOT/'runtime'))=='enterprise_prd/prototype/version.py':data=data.replace(b'0.2.0',version.encode())
        entries.append((str(f.relative_to(ROOT)),data))
    skills=release/f'enterprise-prototype-skills-{version}.zip';zip_tree(skills,entries)
    entries=[]
    for f in files(ROOT/'packs/company-prototype'):
        rel=str(f.relative_to(ROOT/'packs/company-prototype'));data=f.read_bytes()
        if rel in ('pack.yaml','surfaces/surface-guidance.yaml'):data=data.replace(b'0.2.0',version.encode())
        entries.append((rel,data))
    pack=release/f'company-prototype-{version}.zip';zip_tree(pack,entries)
    manifest=kit/'registry/prototype-canary-manifest.yaml'
    manifest.write_text(f'''schema_version: 1
channel: canary
skill:
  name: enterprise-prototype-skills
  version: {version}
  archive: releases/{skills.name}
  sha256: {sha(skills)}
company_pack:
  name: company-prototype
  version: {version}
  archive: releases/{pack.name}
  sha256: {sha(pack)}
compatible_skill: '>=0.1.19,<1.0.0'
published_by: prototype-maintainer
published_at: '2026-01-01T00:00:00+00:00'
force_update: true
''')
    frozen={str(p.relative_to(kit)):sha(p) for p in (skills,pack,manifest)}
    for f in files(ROOT/'bootstrap'):
        rel=f.relative_to(ROOT/'bootstrap');data=f.read_bytes()
        if str(rel)=='prototype-kit.py':
            data=data.replace(b'VERSION = "0.2.0"',f'VERSION = "{version}"'.encode())
            data=data.replace(b'FROZEN = {}  # Filled by deterministic release builder.',('FROZEN = '+repr(frozen)).encode())
        p=kit/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    dump(kit/'kit-manifest.json',{'schema_version':1,'candidate_version':version,'kit_revision':1,'files':{str(p.relative_to(kit)):sha(p) for p in files(kit)}})
    plugin=output/'plugins/prototype-workflow';plugin.mkdir(parents=True)
    for name in ('prototype-setup','prototype-workflow-entry'):
        p=plugin/'skills'/name/'SKILL.md';p.parent.mkdir(parents=True);p.write_bytes((ROOT/'skills'/name/'SKILL.md').read_bytes().replace(b'0.2.0',version.encode()))
    shutil.copytree(ROOT/'plugin_runtime',plugin/'plugin_runtime',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    package=plugin/f'packages/prototype-workflow-kit-{version}.zip';zip_tree(package,[(str(p.relative_to(kit)),p.read_bytes()) for p in files(kit)])
    approved={'schema_version':1,'distribution_version':version,'plugin_name':'prototype-workflow','marketplace_name':'qqx-prototype','package_path':str(package.relative_to(plugin)),'package_sha256':sha(package),'skill_sha256':sha(skills),'pack_sha256':sha(pack)}
    dump(plugin/'release/approved.json',approved);dump(output/'release/approved.json',approved)
    dump(plugin/'plugin.json',{'$schema':'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json','name':'prototype-workflow','version':version,'description':'原型工作流：初始化、新需求、持续修改与交付','extensions':{'com.openai':{'onboardingSkill':'./skills/prototype-setup/SKILL.md'}}})
    dump(output/'.agents/plugins/marketplace.json',{'name':'qqx-prototype','interface':{'displayName':'原型交互工具包'},'plugins':[{'name':'prototype-workflow','source':{'source':'local','path':'./plugins/prototype-workflow'},'policy':{'installation':'AVAILABLE','authentication':'ON_INSTALL'},'category':'Productivity'}]})
    hits=audit_tree(output,[])
    if hits:raise ValueError(json.dumps(hits))
    return {str(p.relative_to(output)):sha(p) for p in files(output)}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--version',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();print(json.dumps(build(a.version,a.output),sort_keys=True))
