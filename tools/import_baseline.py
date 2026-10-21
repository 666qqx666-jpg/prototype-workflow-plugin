"""Import a verified baseline into PRIVATE staging; never copy its repository history."""
import hashlib,io,json,zipfile
from pathlib import Path,PurePosixPath
KIT_SHA='86aeef51b03ac336c562735c4a6d0dbab9d9a366fe6fae62f65e99933b7d6886'
SKILL_SHA='4ded580e4554f5ec3ad44a47c303da465b44456badac2a4891f217c8662a3b78'
PACK_SHA='cf1231e1ecc294d72ffb2a2682a0233d0c52b60355418584fb6a7baa627b96a4'
SKILLS={'prototype-design-workflow','enterprise-prototype-chain','enterprise-prototype-builder','enterprise-prototype-review','enterprise-prototype-code-query'}
def sha(data):return hashlib.sha256(data).hexdigest()
def archive_names(archive):
    seen=set()
    for item in archive.infolist():
        p=PurePosixPath(item.filename)
        if p.is_absolute() or '..' in p.parts or item.filename in seen or str(p)!=item.filename.rstrip('/') or '\\' in item.filename:
            raise ValueError('UNSAFE_ARCHIVE')
        if (item.external_attr>>16)&0o170000==0o120000:raise ValueError('SYMLINK_ARCHIVE')
        seen.add(item.filename);yield item,p

def import_baseline(frozen_kit,destination):
    data=frozen_kit.read_bytes()
    if sha(data)!=KIT_SHA:raise ValueError('BASELINE_MISMATCH')
    destination.mkdir(parents=True,exist_ok=False);records=[]
    def put(rel,data,source):
        out=data
        if rel=='runtime/enterprise_prd/prototype/version.py':out=out.replace(b'0.1.19',b'0.2.0')
        if rel in ('packs/company-prototype/pack.yaml','packs/company-prototype/surfaces/surface-guidance.yaml'):out=out.replace(b'0.1.19',b'0.2.0')
        path=destination/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(out)
        records.append({'source':source,'path':rel,'original_sha256':sha(data),'import_sha256':sha(out),'change':'identity' if out!=data else 'unchanged'})
    with zipfile.ZipFile(io.BytesIO(data)) as outer:
        entries=list(archive_names(outer));prefix=entries[0][1].parts[0]
        for item,p in entries:
            rel=PurePosixPath(*p.parts[1:]).as_posix()
            if rel=='prototype-kit.py' or rel.startswith('vendor/'):
                put('bootstrap/'+rel,outer.read(item),rel)
        for name,expected,kind in [('enterprise-prototype-skills',SKILL_SHA,'skill'),('company-prototype',PACK_SHA,'pack')]:
            raw=outer.read(f'{prefix}/registry/releases/{name}-0.1.19.zip')
            if sha(raw)!=expected:raise ValueError('BASELINE_MISMATCH')
            with zipfile.ZipFile(io.BytesIO(raw)) as inner:
                for item,p in archive_names(inner):
                    if item.is_dir():continue
                    if kind=='skill':
                        if p.parts[0] in SKILLS: rel='skills/'+str(p)
                        elif p.parts[:2]==('runtime','enterprise_prd'):rel=str(p)
                        else:raise ValueError('UNLISTED_SOURCE')
                        if p.parts[:2]==('runtime','enterprise_prd') and 'prototype' not in p.parts and p.name in ('verify.py','verify_genesis.py','verify_reboot.py','feishu_verify.py','feishu_outline_contract_verify.py'):continue
                    else:rel='packs/company-prototype/'+str(p)
                    put(rel,inner.read(item),name+'/'+str(p))
    (destination/'origin.json').write_text(json.dumps(records,indent=2)+'\n')
    return destination
