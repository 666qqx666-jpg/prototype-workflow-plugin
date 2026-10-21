import hashlib,json,re,uuid
from pathlib import Path,PurePosixPath
RELEASE_KEYS=set('schema_version distribution_version plugin_name marketplace_name package_path package_sha256 skill_sha256 pack_sha256'.split())
BINDING_KEYS=set('schema_version requirement_id distribution_version package_sha256 runtime_root executor_sha256 skill_root skill_content_sha256 pack_root pack_content_sha256 manifest_path manifest_sha256 start_here_path start_here_sha256'.split())
class WorkflowError(ValueError):pass

def unique(pairs):
    result={}
    for k,v in pairs:
        if k in result:raise WorkflowError('DUPLICATE_FIELD')
        result[k]=v
    return result

def read(path):
    try:return json.loads(Path(path).read_text(),object_pairs_hook=unique)
    except (OSError,json.JSONDecodeError) as e:raise WorkflowError('INVALID_DOCUMENT') from e

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def version(v):
    if not isinstance(v,str) or not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)',v):raise WorkflowError('INVALID_VERSION')
    return tuple(map(int,v.split('.')))
def closed(d,keys):
    if not isinstance(d,dict) or set(d)!=keys or type(d.get('schema_version')) is not int or d['schema_version']!=1:raise WorkflowError('INVALID_SCHEMA')
def hashes(d):
    for k,v in d.items():
        if k.endswith('sha256') and (not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v)):raise WorkflowError('INVALID_DIGEST')
def release(d):
    closed(d,RELEASE_KEYS);version(d['distribution_version']);hashes(d)
    if d['plugin_name']!='prototype-workflow' or d['marketplace_name']!='qqx-prototype':raise WorkflowError('INVALID_RELEASE')
    p=PurePosixPath(d['package_path'])
    if p.is_absolute() or '..' in p.parts or str(p)!=d['package_path'] or '\\' in str(p) or p.suffix!='.zip':raise WorkflowError('INVALID_PACKAGE_PATH')
    return d

def binding(d):
    closed(d,BINDING_KEYS);version(d['distribution_version']);hashes(d)
    try:
        if str(uuid.UUID(d['requirement_id']))!=d['requirement_id']:raise ValueError()
    except (ValueError,TypeError,AttributeError):raise WorkflowError('INVALID_REQUIREMENT_ID') from None
    for k in ('runtime_root','skill_root','pack_root','manifest_path','start_here_path'):
        p=Path(d[k])
        if not p.is_absolute() or str(p.resolve())!=d[k]:raise WorkflowError('INVALID_BINDING_PATH')
    return d

def archive_names(z):
    seen=set()
    for item in z.infolist():
        p=PurePosixPath(item.filename)
        if p.is_absolute() or '..' in p.parts or str(p)!=item.filename.rstrip('/') or '\\' in item.filename or item.filename in seen or (item.external_attr>>16)&0o170000==0o120000:raise WorkflowError('UNSAFE_ARCHIVE')
        seen.add(item.filename);yield item
