from __future__ import annotations
import fcntl,json,os,stat,tempfile
from contextlib import contextmanager
from pathlib import Path
from enterprise_prd.errors import ContractError
from .errors import ConcurrentRegistryConflict
from .snapshot import RegistrySnapshot
class RegistryStore:
 def __init__(self,root:Path):
  if root.exists() and root.is_symlink():raise ContractError('registry root cannot be a symlink')
  root.mkdir(parents=True,exist_ok=True,mode=0o700);self.root=root.resolve();self.state_path=self.root/'registry.json';self.lock_path=self.root/'.registry.lock'
 @contextmanager
 def _lock(self):
  flags=os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0)
  try:fd=os.open(self.lock_path,flags,0o600)
  except OSError as e:raise ContractError('registry lock cannot be opened safely') from e
  try:
   if not stat.S_ISREG(os.fstat(fd).st_mode):raise ContractError('registry lock is not a regular file')
   fcntl.flock(fd,fcntl.LOCK_EX);yield
  finally:fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)
 def _load_unlocked(self):
  if not self.state_path.is_file() or self.state_path.is_symlink():raise ContractError('registry state is unavailable')
  return RegistrySnapshot.from_mapping(json.loads(self.state_path.read_text()))
 def load(self):
  with self._lock():return self._load_unlocked()
 def _replace(self,v):
  fd,tmp=tempfile.mkstemp(prefix='.registry-',suffix='.json',dir=self.root)
  try:
   os.fchmod(fd,0o600);os.write(fd,v.view_bytes+b'\n');os.fsync(fd);os.close(fd);fd=-1;os.replace(tmp,self.state_path)
  finally:
   if fd!=-1:os.close(fd)
   Path(tmp).unlink(missing_ok=True)
 def _install_no_replace(self,value):
  value.validate();descriptor,temporary=tempfile.mkstemp(prefix='.registry-exclusive-',suffix='.json',dir=self.root)
  try:
   os.fchmod(descriptor,0o600);payload=value.view_bytes+b'\n';offset=0
   while offset<len(payload):offset+=os.write(descriptor,payload[offset:])
   os.fsync(descriptor);os.close(descriptor);descriptor=-1
   os.link(temporary,self.state_path,follow_symlinks=False)
   directory=os.open(self.root,os.O_RDONLY)
   try:os.fsync(directory)
   finally:os.close(directory)
  finally:
   if descriptor!=-1:os.close(descriptor)
   Path(temporary).unlink(missing_ok=True)
 def create_exclusive(self,value):
  if type(value) is not RegistrySnapshot:raise ContractError('registry snapshot is invalid')
  value.validate()
  with self._lock():
   if os.path.lexists(self.state_path):raise ConcurrentRegistryConflict('registry already exists')
   try:self._install_no_replace(value)
   except FileExistsError as exc:raise ConcurrentRegistryConflict('registry already exists') from exc
 def create(self,v):
  with self._lock():
   if self.state_path.exists():
    if self._load_unlocked().view_hash!=v.view_hash:raise ConcurrentRegistryConflict('registry already exists with different bytes')
    return
   self._replace(v)
 def commit(self,expected_view_hash,v):
  with self._lock():
   if self._load_unlocked().view_hash!=expected_view_hash:raise ConcurrentRegistryConflict('registry view changed')
   self._replace(v)
