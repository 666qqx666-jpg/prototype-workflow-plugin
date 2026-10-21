"""Closed, write-once company configuration. Contains no service defaults."""
import base64,hashlib,ipaddress,json,os,re,shutil,tempfile
from pathlib import Path

def unique(pairs):
    out={}
    for k,v in pairs:
        if k in out:raise ValueError('DUPLICATE_FIELD')
        out[k]=v
    return out

def digest(data):return hashlib.sha256(data).hexdigest()
def read_json(path):
    raw=path.read_bytes()
    if len(raw)>65536:raise ValueError('PROFILE_TOO_LARGE')
    return json.loads(raw,object_pairs_hook=unique)
def closed(obj,keys):return isinstance(obj,dict) and set(obj)==set(keys)
def validate_profile(p):
    if not closed(p,('schema_version','service','known_hosts')) or type(p['schema_version']) is not int or p['schema_version']!=1:raise ValueError('INVALID_PROFILE')
    s=p['service']
    if not closed(s,('schema_version','host','port','remote_script','known_hosts_sha256')) or type(s['schema_version']) is not int or s['schema_version']!=2 or type(s['port']) is not int or s['port']!=22:raise ValueError('INVALID_SERVICE')
    if not isinstance(s['host'],str) or str(ipaddress.IPv4Address(s['host']))!=s['host']:raise ValueError('INVALID_HOST')
    script=s['remote_script']
    if not isinstance(script,str) or not re.fullmatch(r'/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.js',script) or any(x in ('.','..') for x in script.split('/')[1:]):raise ValueError('INVALID_SCRIPT')
    known=p['known_hosts']
    if not isinstance(known,str) or len(known.splitlines())!=1 or '\r' in known:raise ValueError('INVALID_KNOWN_HOSTS')
    fields=known.split()
    if len(fields)!=3 or fields[:2]!=[s['host'],'ssh-ed25519']:raise ValueError('INVALID_KNOWN_HOSTS')
    blob=base64.b64decode(fields[2],validate=True)
    if len(blob)!=51 or blob[:19]!=b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20':raise ValueError('INVALID_HOST_KEY')
    if s['known_hosts_sha256']!=digest(known.encode()):raise ValueError('KNOWN_HOSTS_MISMATCH')
    return p

def load_profile(path):
    path=Path(path)
    if '.ssh' in path.resolve().parts or path.is_symlink():raise ValueError('UNSAFE_PROFILE_PATH')
    if not path.is_file() or path.stat().st_size>65536:raise ValueError('INVALID_PROFILE_FILE')
    return validate_profile(read_json(path))
def root(home):return Path(home)/'.config/prototype-workflow/company'
def reject_symlinks(path):
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('CONFIGURATION_SYMLINK')
def _bytes(p):return (json.dumps(p,sort_keys=True,separators=(',',':'))+'\n').encode()
def import_profile(home,source):
    p=load_profile(source);destination=root(home);reject_symlinks(destination)
    if destination.exists():
        current=capture_profile(home)
        if current['profile']!=p:raise ValueError('PROFILE_REPLACEMENT_REFUSED')
        return current
    destination.parent.mkdir(parents=True,exist_ok=True);destination.parent.chmod(0o700)
    staged=Path(tempfile.mkdtemp(prefix='.company-',dir=destination.parent))
    try:
        staged.chmod(0o700)
        for name,data in [('profile.json',_bytes(p)),('service.json',_bytes(p['service'])),('known_hosts',p['known_hosts'].encode())]:
            f=staged/name;fd=os.open(f,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            f.chmod(0o400)
        # rename refuses an existing nonempty config directory; never replaces files.
        os.rename(staged,destination)
    finally:
        if staged.exists():shutil.rmtree(staged)
    return capture_profile(home)

def capture_profile(home):
    directory=root(home);reject_symlinks(directory)
    stamps={};contents={}
    for name in ('profile.json','service.json','known_hosts'):
        f=directory/name;reject_symlinks(f);before=f.stat()
        if not f.is_file() or before.st_uid!=os.getuid() or before.st_mode&0o777!=0o400:raise ValueError('PROFILE_PERMISSIONS')
        data=f.read_bytes();after=f.stat()
        stamp=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if stamp(before)!=stamp(after):raise ValueError('PROFILE_CHANGED')
        stamps[name]=(digest(data),stamp(before));contents[name]=data
    p=validate_profile(json.loads(contents['profile.json'],object_pairs_hook=unique))
    if json.loads(contents['service.json'],object_pairs_hook=unique)!=p['service'] or contents['known_hosts']!=p['known_hosts'].encode():raise ValueError('PROFILE_CHANGED')
    return {'profile':p,'company_profile_sha256':digest(contents['profile.json']),'stamps':stamps}
