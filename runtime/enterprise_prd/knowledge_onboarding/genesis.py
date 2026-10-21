from __future__ import annotations
import fcntl,json,os,stat,uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from enterprise_prd.baseline_registry.store import RegistryStore
from enterprise_prd.canonical import canonical_json,sha256_bytes
from enterprise_prd.contracts import HASH_RE,runtime_id
from enterprise_prd.errors import ContractError
from .contracts import OnboardingSession
from .errors import OnboardingError
GENESIS_BASE_MAPPING={'bindings':[],'conflicts':[],'dependencies':[],'documents':[],'revisions':[],'schema_version':1,'surfaces':[]}
GENESIS_BASE_VIEW_HASH='ac926ab2b2d7a4b73338906ac1a271b1c561aaa67b190bf24adeaa5fa2459573'
if sha256_bytes(canonical_json(GENESIS_BASE_MAPPING))!=GENESIS_BASE_VIEW_HASH:raise RuntimeError('Genesis base hash is invalid')
CLAIM_FIELDS={'schema_version','component','onboarding_id','base_view_hash','proposed_registry_view_hash','governance_hash','selection_hash'}
def is_genesis_session(session:OnboardingSession)->bool:return session.expected_registry_view_hash==GENESIS_BASE_VIEW_HASH
def registry_presence(registry:RegistryStore)->tuple[str,str|None]:
 if not os.path.lexists(registry.state_path):return 'absent',None
 return 'present',registry.load().view_hash
@dataclass(frozen=True)
class GenesisClaim:
 onboarding_id:str
 proposed_registry_view_hash:str
 governance_hash:str
 selection_hash:str
 def to_mapping(self)->dict:
  return {'schema_version':1,'component':'real-knowledge-onboarding-genesis','onboarding_id':self.onboarding_id,'base_view_hash':GENESIS_BASE_VIEW_HASH,'proposed_registry_view_hash':self.proposed_registry_view_hash,'governance_hash':self.governance_hash,'selection_hash':self.selection_hash}
 def validate(self)->None:
  runtime_id(self.onboarding_id,'onboarding_id')
  for value in (self.proposed_registry_view_hash,self.governance_hash,self.selection_hash):
   if HASH_RE.fullmatch(value) is None:raise ContractError('Genesis Claim is invalid')
 @classmethod
 def from_mapping(cls,raw:object)->'GenesisClaim':
  if type(raw)is not dict or set(raw)!=CLAIM_FIELDS:raise ContractError('Genesis Claim is invalid')
  if raw['schema_version']!=1 or raw['component']!='real-knowledge-onboarding-genesis' or raw['base_view_hash']!=GENESIS_BASE_VIEW_HASH:raise ContractError('Genesis Claim is invalid')
  value=cls(raw['onboarding_id'],raw['proposed_registry_view_hash'],raw['governance_hash'],raw['selection_hash']);value.validate();return value
class GenesisClaimStore:
 def __init__(self,registry:RegistryStore)->None:
  self.registry=registry;self.path=registry.root/'.genesis-claim.json';info=os.lstat(registry.root)
  if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_mode&0o777!=0o700:raise ContractError('Genesis root is invalid')
 @contextmanager
 def _lock(self):
  flags=os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0)
  try:descriptor=os.open(self.registry.lock_path,flags,0o600)
  except OSError as exc:raise ContractError('Genesis lock is invalid') from exc
  try:
   info=os.fstat(descriptor)
   if not stat.S_ISREG(info.st_mode) or info.st_mode&0o777!=0o600:raise ContractError('Genesis lock is invalid')
   fcntl.flock(descriptor,fcntl.LOCK_EX);yield
  finally:fcntl.flock(descriptor,fcntl.LOCK_UN);os.close(descriptor)
 def _read_unlocked(self)->GenesisClaim|None:
  if not os.path.lexists(self.path):return None
  try:descriptor=os.open(self.path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
  except OSError as exc:raise ContractError('Genesis Claim is invalid') from exc
  try:
   info=os.fstat(descriptor)
   if not stat.S_ISREG(info.st_mode) or info.st_mode&0o777!=0o600 or info.st_size>16*1024:raise ContractError('Genesis Claim is invalid')
   payload=os.read(descriptor,16*1024+1)
  finally:os.close(descriptor)
  try:value=GenesisClaim.from_mapping(json.loads(payload.decode('utf-8')))
  except (UnicodeDecodeError,json.JSONDecodeError) as exc:raise ContractError('Genesis Claim is invalid') from exc
  if payload!=canonical_json(value.to_mapping())+b'\n':raise ContractError('Genesis Claim is invalid')
  return value
 def load_optional(self)->GenesisClaim|None:
  with self._lock():return self._read_unlocked()
 def install(self,claim:GenesisClaim)->GenesisClaim:
  claim.validate();expected=canonical_json(claim.to_mapping())+b'\n'
  with self._lock():
   current=self._read_unlocked()
   if current is not None:
    if current!=claim:raise OnboardingError('genesis_claim_conflict')
    return current
   if os.path.lexists(self.registry.state_path):raise OnboardingError('genesis_precondition_changed')
   temporary=self.registry.root/f'.genesis-claim-{uuid.uuid4().hex}.json';flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0)
   try:descriptor=os.open(temporary,flags,0o600)
   except OSError as exc:raise ContractError('Genesis Claim is invalid') from exc
   try:
    os.fchmod(descriptor,0o600);offset=0
    while offset<len(expected):offset+=os.write(descriptor,expected[offset:])
    os.fsync(descriptor);os.close(descriptor);descriptor=-1
    try:os.link(temporary,self.path,follow_symlinks=False)
    except FileExistsError:
     raced=self._read_unlocked()
     if raced!=claim:raise OnboardingError('genesis_claim_conflict')
     return raced
    except OSError as exc:raise ContractError('Genesis Claim is invalid') from exc
    try:directory=os.open(self.registry.root,os.O_RDONLY)
    except OSError as exc:raise ContractError('Genesis Claim is invalid') from exc
    try:os.fsync(directory)
    finally:os.close(directory)
   finally:
    if descriptor!=-1:os.close(descriptor)
    Path(temporary).unlink(missing_ok=True)
  return claim
