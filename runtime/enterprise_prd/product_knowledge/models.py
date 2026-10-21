from __future__ import annotations

from dataclasses import dataclass, field

from enterprise_prd.contracts import (
    HASH_RE,
    AccessDecision,
    StrEnum,
    aware_timestamp,
    exact_strings,
    need,
    runtime_id,
)
from enterprise_prd.errors import ContractError


class ContextBinding(StrEnum):
    CURRENT_SURFACE = "current-surface"
    REFERENCE_CAPABILITY = "reference-capability"
    CURRENT_PRD = "current-prd"
    SUPPORTING_REFERENCE = "supporting-reference"


class AcquisitionScope(StrEnum):
    NATIVE_SECTIONS = "native_sections"
    FULL_DOCUMENT_LOCAL_EXTRACT = "full_document_local_extract"


class Lifecycle(StrEnum):
    UNKNOWN = "unknown"
    CURRENT = "current"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"
    UNAVAILABLE = "unavailable"


class SelectionStatus(StrEnum):
    CREATED = "created"
    CANDIDATES_READY = "candidates_ready"
    DOCUMENTS_CONFIRMED = "documents_confirmed"
    OUTLINES_READY = "outlines_ready"
    SECTIONS_CONFIRMED = "sections_confirmed"
    GRANT_READY = "grant_ready"
    CONSUMED = "consumed"
    SOURCE_SELECTION_INVALID = "source_selection_invalid"
    REVISION_CHANGED = "revision_changed"
    ACCESS_DENIED = "access_denied"
    CANCELLED = "cancelled"


class SelectionEventType(StrEnum):
    SESSION_CREATED = "session_created"
    CANDIDATES_SET = "candidates_set"
    DOCUMENTS_CONFIRMED = "documents_confirmed"
    OUTLINES_RECORDED = "outlines_recorded"
    SECTIONS_CONFIRMED = "sections_confirmed"
    GRANT_READY = "grant_ready"
    GRANT_ABANDONED = "grant_abandoned"
    CONTEXT_CONSUMED = "context_consumed"
    SOURCE_SELECTION_INVALID = "source_selection_invalid"
    REVISION_CHANGED = "revision_changed"
    ACCESS_DENIED = "access_denied"
    CANCELLED = "cancelled"


class ProjectionStatus(StrEnum):
    VALID = "valid"
    STALE = "stale"
    SUPERSEDED = "superseded"
    UNAVAILABLE = "unavailable"


class ProjectionInvalidationReason(StrEnum):
    REVISION_CHANGED = "revision_changed"
    REGISTRY_SUPERSEDED = "registry_superseded"
    SOURCE_UNAVAILABLE = "source_unavailable"


class GrantReceiptStatus(StrEnum):
    RESERVED = "reserved"
    CONSUMED = "consumed"
    FAILED = "failed"
    REVISION_CHANGED = "revision_changed"
    ACCESS_DENIED = "access_denied"


def _hash(value: str, field_name: str) -> None:
    need(type(value) is str and HASH_RE.fullmatch(value) is not None, f"{field_name} is invalid")


@dataclass(frozen=True)
class KnowledgeNeed:
    requirement: str
    required_bindings: tuple[ContextBinding, ...]
    optional_bindings: tuple[ContextBinding, ...]

    def validate(self) -> None:
        need(type(self.requirement) is str and bool(self.requirement.strip()), "requirement is required")
        need(type(self.required_bindings) is tuple and type(self.optional_bindings) is tuple, "knowledge need bindings must be tuples")
        values = self.required_bindings + self.optional_bindings
        need(bool(values) and all(type(value) is ContextBinding for value in values), "bindings are invalid")
        exact_strings(tuple(value.value for value in values), "knowledge need bindings")
        need(not set(self.required_bindings) & set(self.optional_bindings), "required and optional bindings overlap")


@dataclass(frozen=True)
class DocumentCandidate:
    source_id: str
    binding: ContextBinding
    reason: str

    def validate(self) -> None:
        need(all(type(value) is str and value.strip() for value in (self.source_id, self.reason)), "candidate fields are required")
        need(type(self.binding) is ContextBinding, "candidate binding is invalid")


@dataclass(frozen=True)
class ConfirmedDocument:
    source_id: str
    binding: ContextBinding
    required: bool


@dataclass(frozen=True)
class AdapterHeading:
    block_id: str
    level: int
    raw_title: str
    normalized_title: str
    raw_path: str
    normalized_path: str

    def validate(self) -> None:
        need(type(self.block_id) is str and bool(self.block_id), "heading block_id is required")
        need(type(self.level) is int and 1 <= self.level <= 6, "heading level is invalid")
        need(all(type(value) is str and bool(value) for value in (self.raw_title, self.normalized_title, self.raw_path, self.normalized_path)), "heading path is required")


@dataclass(frozen=True)
class AdapterOutline:
    document_id: str
    title: str
    revision: str
    headings: tuple[AdapterHeading, ...]


@dataclass(frozen=True)
class AdapterSection:
    document_id: str
    block_id: str
    revision: str
    content: str
    content_sha256: str


@dataclass(frozen=True)
class SourceOutline:
    schema_version: int
    projection_type: str
    extractor_version: str
    registry_sha256: str
    source_id: str
    document_id_sha256: str
    title: str
    revision: str
    adapter: str
    generated_at: str
    headings: tuple[AdapterHeading, ...]
    outline_sha256: str


@dataclass(frozen=True)
class RequestedSection:
    source_id: str
    binding: ContextBinding
    heading_path: str


@dataclass(frozen=True)
class SectionSelection:
    source_id: str
    binding: ContextBinding
    block_id: str
    raw_path: str
    normalized_path: str
    heading_level: int
    acquisition_scope: AcquisitionScope
    remote_read: bool


@dataclass(frozen=True)
class SourceObservation:
    source_id: str
    adapter: str
    document_id_sha256: str
    title: str
    revision: str
    modified_at: str | None
    access: AccessDecision
    observed_at: str


@dataclass(frozen=True)
class ReadGrant:
    schema_version: int
    grant_id: str
    session_id: str
    run_id: str
    run_snapshot_sha256: str
    registry_sha256: str
    selection_sha256: str
    required_bindings: tuple[ContextBinding, ...]
    optional_bindings: tuple[ContextBinding, ...]
    missing_optional_bindings: tuple[ContextBinding, ...]
    issued_at: str
    sources: tuple[dict, ...]
    grant_sha256: str

    def validate(self) -> None:
        need(self.schema_version == 1, "grant schema is invalid")
        runtime_id(self.grant_id, "grant_id")
        runtime_id(self.session_id, "session_id")
        runtime_id(self.run_id, "run_id")
        for value, name in ((self.run_snapshot_sha256, "run snapshot hash"), (self.registry_sha256, "registry hash"), (self.selection_sha256, "selection hash"), (self.grant_sha256, "grant hash")):
            _hash(value, name)
        aware_timestamp(self.issued_at, "grant issued_at")


@dataclass(frozen=True)
class SelectionState:
    session_id: str
    run_id: str
    run_snapshot_sha256: str
    registry_sha256: str
    need: KnowledgeNeed
    status: SelectionStatus
    candidates: tuple[DocumentCandidate, ...] = ()
    confirmed_documents: tuple[ConfirmedDocument, ...] = ()
    outlines: tuple[SourceOutline, ...] = ()
    section_selections: tuple[SectionSelection, ...] = ()
    missing_optional_bindings: tuple[ContextBinding, ...] = ()
    active_grant_id: str | None = None
    event_sha256: str = ""

    def validate(self) -> None:
        runtime_id(self.session_id, "session_id")
        runtime_id(self.run_id, "run_id")
        _hash(self.run_snapshot_sha256, "run snapshot hash")
        _hash(self.registry_sha256, "registry hash")
        self.need.validate()
        need(type(self.status) is SelectionStatus, "selection status is invalid")
        if self.active_grant_id is not None:
            runtime_id(self.active_grant_id, "grant_id")
