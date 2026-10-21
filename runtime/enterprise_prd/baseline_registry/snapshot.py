from __future__ import annotations
from dataclasses import dataclass, replace, asdict
from pathlib import Path
import yaml
from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.errors import ContractError
from . import contracts as c

FIELDS={"documents":{"source_id","display_name","adapter","locator_ref","lifecycle_status","observed_revision","active_revision","released_revision","record_version"},"revisions":{"source_id","revision","observed_at","source_kind","content_bytes","observation_state"},"surfaces":{"surface_id","display_path","record_version"},"bindings":{"source_id","surface_id","state","change_mode","impact_summary","based_on_revision","confirmed_by","confirmed_at","record_version"},"dependencies":{"source_id","depends_on","state","rationale","confirmed_by","confirmed_at","record_version"},"conflicts":{"resolution_id","surface_id","source_ids","scope","decision","winner_source_id","excluded_source_ids","state","confirmed_by","confirmed_at","record_version"}}
ROOT=set(FIELDS)|{"schema_version"}
def _e(t,v):
 try:return t(v)
 except (TypeError,ValueError) as x:raise ContractError("registry enum is invalid") from x
@dataclass(frozen=True)
class RegistrySnapshot:
 documents:tuple[c.DocumentNode,...]; revisions:tuple[c.RevisionRecord,...]; surfaces:tuple[c.ProductSurface,...]; bindings:tuple[c.SurfaceBinding,...]; dependencies:tuple[c.DependencyEdge,...]; conflicts:tuple[c.ConflictResolution,...]
 @classmethod
 def load_yaml(cls,path:Path): return cls.from_mapping(yaml.safe_load(path.read_text(encoding="utf-8")))
 @classmethod
 def from_mapping(cls,raw):
  if type(raw)is not dict or set(raw)!=ROOT or raw["schema_version"]!=1:raise ContractError("registry root fields are invalid")
  for k in FIELDS:
   if type(raw[k])is not list or any(type(x)is not dict or set(x)!=FIELDS[k] for x in raw[k]):raise ContractError(f"registry {k} fields are invalid")
  docs=tuple(c.DocumentNode(x["source_id"],x["display_name"],x["adapter"],x["locator_ref"],_e(c.LifecycleStatus,x["lifecycle_status"]),x["observed_revision"],x["active_revision"],x["released_revision"],x["record_version"]) for x in raw["documents"])
  revs=tuple(c.RevisionRecord(x["source_id"],x["revision"],x["observed_at"],x["source_kind"],x["content_bytes"],_e(c.ObservationState,x["observation_state"])) for x in raw["revisions"])
  surfs=tuple(c.ProductSurface(x["surface_id"],tuple(x["display_path"]),x["record_version"]) for x in raw["surfaces"])
  binds=tuple(c.SurfaceBinding(x["source_id"],x["surface_id"],_e(c.BindingState,x["state"]),_e(c.ChangeMode,x["change_mode"]),x["impact_summary"],x["based_on_revision"],x["confirmed_by"],x["confirmed_at"],x["record_version"]) for x in raw["bindings"])
  deps=tuple(c.DependencyEdge(x["source_id"],x["depends_on"],_e(c.DependencyState,x["state"]),x["rationale"],x["confirmed_by"],x["confirmed_at"],x["record_version"]) for x in raw["dependencies"])
  conf=tuple(c.ConflictResolution(x["resolution_id"],x["surface_id"],tuple(x["source_ids"]),_e(c.ConflictScope,x["scope"]),_e(c.ConflictDecision,x["decision"]),x["winner_source_id"],tuple(x["excluded_source_ids"]),_e(c.BindingState,x["state"]),x["confirmed_by"],x["confirmed_at"],x["record_version"]) for x in raw["conflicts"])
  v=cls(tuple(sorted(docs,key=lambda x:x.source_id)),tuple(sorted(revs,key=lambda x:(x.source_id,x.revision))),tuple(sorted(surfs,key=lambda x:x.surface_id)),tuple(sorted(binds,key=lambda x:(x.source_id,x.surface_id))),tuple(sorted(deps,key=lambda x:(x.source_id,x.depends_on))),tuple(sorted(conf,key=lambda x:x.resolution_id)));v.validate();return v
 def validate(self):
  for xs,key in ((self.documents,lambda x:x.source_id),(self.revisions,lambda x:(x.source_id,x.revision)),(self.surfaces,lambda x:x.surface_id),(self.bindings,lambda x:(x.source_id,x.surface_id)),(self.dependencies,lambda x:(x.source_id,x.depends_on)),(self.conflicts,lambda x:x.resolution_id)):
   if len({key(x) for x in xs})!=len(xs):raise ContractError("registry identities must be unique")
   for x in xs:x.validate()
  ids={x.source_id for x in self.documents};sids={x.surface_id for x in self.surfaces};revs={(x.source_id,x.revision) for x in self.revisions}
  if not ids or not revs or not sids:raise ContractError("registry core collections are empty")
  for x in self.documents:
   if any((x.source_id,r) not in revs for r in (x.observed_revision,x.active_revision,x.released_revision) if r is not None):raise ContractError("document revision is not recorded")
  for x in self.bindings:
   if x.source_id not in ids or x.surface_id not in sids or (x.source_id,x.based_on_revision) not in revs:raise ContractError("binding reference is invalid")
  for x in self.dependencies:
   if x.source_id not in ids or x.depends_on not in ids:raise ContractError("dependency reference is invalid")
  for x in self.conflicts:
   if x.surface_id not in sids or not set(x.source_ids)<=ids:raise ContractError("conflict reference is invalid")
 def to_mapping(self):
  def cv(x):
   d=asdict(x)
   for k,v in tuple(d.items()):
    if hasattr(v,"value"):d[k]=v.value
    elif isinstance(v,tuple):d[k]=list(v)
   return d
  return {"schema_version":1,**{k:[cv(x) for x in sorted(getattr(self,k),key=lambda x:str(x))] for k in FIELDS}}
 @property
 def view_bytes(self):return canonical_json(self.to_mapping())
 @property
 def view_hash(self):return sha256_bytes(self.view_bytes)
 def document(self,source_id):
  xs=[x for x in self.documents if x.source_id==source_id]
  if len(xs)!=1:raise ContractError("source is outside Registry")
  return xs[0]
 def revision(self,source_id,revision):
  xs=[x for x in self.revisions if (x.source_id,x.revision)==(source_id,revision)]
  if len(xs)!=1:raise ContractError("revision is outside Registry")
  return xs[0]
 def replace_binding(self,value,*,expected_record_version):
  value.validate();old=next((x for x in self.bindings if (x.source_id,x.surface_id)==(value.source_id,value.surface_id)),None)
  if old is None or old.record_version!=expected_record_version or value.record_version!=expected_record_version+1:raise ContractError("binding record_version changed")
  return RegistrySnapshot.from_mapping({**self.to_mapping(),"bindings":[(lambda x: {**x,"state":value.state.value,"change_mode":value.change_mode.value,"confirmed_by":value.confirmed_by,"confirmed_at":value.confirmed_at,"record_version":value.record_version})(x) if (x["source_id"],x["surface_id"])==(value.source_id,value.surface_id) else x for x in self.to_mapping()["bindings"]]})
 def observe(self,record,*,expected_record_version):
  record.validate();old=self.document(record.source_id)
  if old.record_version!=expected_record_version:raise ContractError("document record_version changed")
  matches=[x for x in self.revisions if (x.source_id,x.revision)==(record.source_id,record.revision)]
  if matches:
   if matches[0]!=record:raise ContractError("immutable revision metadata conflicts")
   if old.observed_revision==record.revision:return self
   raise ContractError("recorded revision cannot become a new observation")
  raw=self.to_mapping();raw["revisions"].append({"source_id":record.source_id,"revision":record.revision,"observed_at":record.observed_at,"source_kind":record.source_kind,"content_bytes":record.content_bytes,"observation_state":record.observation_state.value})
  for x in raw["documents"]:
   if x["source_id"]==record.source_id:x["observed_revision"]=record.revision;x["record_version"]+=1
  return RegistrySnapshot.from_mapping(raw)
