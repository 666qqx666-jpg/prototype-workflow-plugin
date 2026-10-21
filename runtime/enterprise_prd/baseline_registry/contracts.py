from __future__ import annotations
import re
from dataclasses import dataclass
from enterprise_prd.contracts import HASH_RE, IDENTITY_RE, StrEnum, aware_timestamp, need

SURFACE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9-]*)+$")
LOCATOR_REF_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RESOLUTION_ID_RE = re.compile(r"^conflict-[a-z0-9][a-z0-9-]{1,86}$")
FORBIDDEN_SOURCE_IDS = frozenset({"slm-followup-records"})
class LifecycleStatus(StrEnum): UNCONFIRMED="unconfirmed"; UNSCHEDULED="unscheduled"; IN_DEVELOPMENT="in_development"; RELEASED="released"; WITHDRAWN="withdrawn"
class ObservationState(StrEnum): AVAILABLE="available"; UNAVAILABLE="unavailable"
class BindingState(StrEnum): SUGGESTED="suggested"; CONFIRMED="confirmed"; REJECTED="rejected"
class ChangeMode(StrEnum): ADDITIVE="additive"; MODIFIES="modifies"; REMOVES="removes"; UNKNOWN="unknown"
class DependencyState(StrEnum): SUGGESTED="suggested"; CONFIRMED="confirmed"; REJECTED="rejected"
class ConflictScope(StrEnum): GLOBAL="global"; TASK="task"
class ConflictDecision(StrEnum): COMPATIBLE="compatible"; EXCLUDE="exclude"; SOURCE_WINS="source_wins"
class BaselineMode(StrEnum): PRODUCTION="production"; TARGET_DESIGN="target_design"
class BaselineRole(StrEnum): PRODUCTION="production"; EXPLICIT_TARGET="explicit_target"; AUTO_DEPENDENCY="auto_dependency"
def _safe_id(value, field): need(type(value) is str and IDENTITY_RE.fullmatch(value) is not None, f"{field} is invalid")
def _surface_id(value): need(type(value) is str and len(value)<=160 and SURFACE_ID_RE.fullmatch(value) is not None, "surface_id is invalid")
def _resolution_id(value): need(type(value) is str and RESOLUTION_ID_RE.fullmatch(value) is not None, "resolution_id is invalid")
def _hash(value, field): need(type(value) is str and HASH_RE.fullmatch(value) is not None, f"{field} is invalid")
def _version(value): need(type(value) is int and value>=1, "record_version is invalid")
@dataclass(frozen=True)
class DocumentNode:
 source_id:str; display_name:str; adapter:str; locator_ref:str; lifecycle_status:LifecycleStatus; observed_revision:str; active_revision:str|None; released_revision:str|None; record_version:int
 def validate(self):
  _safe_id(self.source_id,"source_id"); need(self.source_id not in FORBIDDEN_SOURCE_IDS,"source identity is quarantined"); need(type(self.display_name) is str and bool(self.display_name.strip()),"display_name is invalid"); _safe_id(self.adapter,"adapter"); need(type(self.locator_ref) is str and LOCATOR_REF_RE.fullmatch(self.locator_ref) is not None,"locator_ref is invalid"); need(type(self.lifecycle_status) is LifecycleStatus,"lifecycle_status is invalid"); _hash(self.observed_revision,"observed_revision")
  if self.active_revision is not None:_hash(self.active_revision,"active_revision")
  if self.released_revision is not None:_hash(self.released_revision,"released_revision")
  if self.lifecycle_status in (LifecycleStatus.UNCONFIRMED,LifecycleStatus.WITHDRAWN): need(self.active_revision is None and self.released_revision is None,"unapproved lifecycle has approved revisions")
  elif self.lifecycle_status is LifecycleStatus.RELEASED: need(self.active_revision is not None and self.released_revision is not None,"released revision is missing")
  else: need(self.active_revision is not None and self.released_revision is None,"non-released lifecycle revisions are invalid")
  _version(self.record_version)
@dataclass(frozen=True)
class RevisionRecord:
 source_id:str; revision:str; observed_at:str; source_kind:str; content_bytes:int; observation_state:ObservationState
 def validate(self): _safe_id(self.source_id,"revision source_id"); _hash(self.revision,"revision"); aware_timestamp(self.observed_at,"observed_at"); _safe_id(self.source_kind,"source_kind"); need(type(self.content_bytes)is int and self.content_bytes>=0,"content_bytes is invalid"); need(type(self.observation_state) is ObservationState,"observation_state is invalid")
@dataclass(frozen=True)
class ProductSurface:
 surface_id:str; display_path:tuple[str,...]; record_version:int
 def validate(self): _surface_id(self.surface_id); need(type(self.display_path)is tuple and self.display_path and all(type(x)is str and x.strip() for x in self.display_path),"display_path is invalid"); _version(self.record_version)
@dataclass(frozen=True)
class SurfaceBinding:
 source_id:str; surface_id:str; state:BindingState; change_mode:ChangeMode; impact_summary:str; based_on_revision:str; confirmed_by:str|None; confirmed_at:str|None; record_version:int
 def validate(self):
  _safe_id(self.source_id,"binding source_id");_surface_id(self.surface_id);need(type(self.state)is BindingState and type(self.change_mode)is ChangeMode,"binding enums are invalid");need(type(self.impact_summary)is str and bool(self.impact_summary.strip()),"impact_summary is invalid");_hash(self.based_on_revision,"based_on_revision")
  if self.state is BindingState.CONFIRMED: need(type(self.confirmed_by)is str and bool(self.confirmed_by.strip()),"binding confirmer is missing");need(type(self.confirmed_at)is str,"binding confirmed_at is missing");aware_timestamp(self.confirmed_at,"binding confirmed_at")
  else: need(self.confirmed_by is None and self.confirmed_at is None,"unconfirmed binding has confirmation fields")
  _version(self.record_version)
@dataclass(frozen=True)
class DependencyEdge:
 source_id:str; depends_on:str; state:DependencyState; rationale:str; confirmed_by:str|None; confirmed_at:str|None; record_version:int
 def validate(self):
  _safe_id(self.source_id,"dependency source_id");_safe_id(self.depends_on,"dependency depends_on");need(self.source_id!=self.depends_on,"dependency self-cycle is invalid");need(type(self.state)is DependencyState,"dependency state is invalid");need(type(self.rationale)is str and bool(self.rationale.strip()),"dependency rationale is invalid")
  if self.state is DependencyState.CONFIRMED: need(type(self.confirmed_by)is str and bool(self.confirmed_by.strip()),"dependency confirmer is missing");need(type(self.confirmed_at)is str,"dependency confirmed_at is missing");aware_timestamp(self.confirmed_at,"dependency confirmed_at")
  else:need(self.confirmed_by is None and self.confirmed_at is None,"unconfirmed dependency has confirmation fields")
  _version(self.record_version)
@dataclass(frozen=True)
class ConflictResolution:
 resolution_id:str;surface_id:str;source_ids:tuple[str,...];scope:ConflictScope;decision:ConflictDecision;winner_source_id:str|None;excluded_source_ids:tuple[str,...];state:BindingState;confirmed_by:str|None;confirmed_at:str|None;record_version:int
 def validate(self):
  _resolution_id(self.resolution_id);_surface_id(self.surface_id);need(type(self.source_ids)is tuple and len(self.source_ids)>=2 and tuple(sorted(self.source_ids))==self.source_ids and len(set(self.source_ids))==len(self.source_ids),"conflict source_ids are invalid");[_safe_id(x,"conflict source_id") for x in self.source_ids];need(type(self.scope)is ConflictScope and type(self.decision)is ConflictDecision,"conflict enums are invalid");need(type(self.excluded_source_ids)is tuple and tuple(sorted(self.excluded_source_ids))==self.excluded_source_ids and set(self.excluded_source_ids)<=set(self.source_ids),"excluded_source_ids are invalid")
  if self.decision is ConflictDecision.COMPATIBLE:need(self.winner_source_id is None and not self.excluded_source_ids,"compatible conflict has outcome fields")
  elif self.decision is ConflictDecision.EXCLUDE:need(self.scope is ConflictScope.TASK and self.winner_source_id is None,"exclude conflict scope is invalid");need(0<len(self.excluded_source_ids)<len(self.source_ids),"exclude conflict needs a partial source set")
  else:need(self.winner_source_id in self.source_ids and not self.excluded_source_ids,"source_wins outcome is invalid")
  need(type(self.state)is BindingState,"conflict state is invalid")
  if self.state is BindingState.CONFIRMED:need(type(self.confirmed_by)is str and bool(self.confirmed_by.strip()),"conflict confirmer is missing");need(type(self.confirmed_at)is str,"conflict confirmed_at is missing");aware_timestamp(self.confirmed_at,"conflict confirmed_at")
  else:need(self.confirmed_by is None and self.confirmed_at is None,"unconfirmed conflict has confirmation fields")
  _version(self.record_version)
