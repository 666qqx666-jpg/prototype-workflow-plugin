from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath

from .errors import ContractError


class StrEnum(str, Enum):
    pass


class UpdateStatus(StrEnum):
    CURRENT = "current"
    UPDATE_AVAILABLE = "update_available"
    DEGRADED_CACHE = "degraded_cache"
    BLOCKED_FORCE_UPDATE = "blocked_force_update"
    FAILED_INTEGRITY = "failed_integrity"


class ReleaseChannel(StrEnum):
    CANARY = "canary"
    STABLE = "stable"


class AccessDecision(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    UNKNOWN = "unknown"


class MissingBehavior(StrEnum):
    BLOCK = "block"
    DEGRADE = "degrade"
    ASK_USER = "ask_user"


class WriteCommand(StrEnum):
    STR_REPLACE = "str_replace"
    BLOCK_REPLACE = "block_replace"
    BLOCK_INSERT_AFTER = "block_insert_after"


HASH_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
RANGE_RE = re.compile(r"^>=(\d+\.\d+\.\d+),<(\d+\.\d+\.\d+)$")
IDENTITY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
RUNTIME_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def need(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def runtime_id(value: object, field: str) -> str:
    """Return a filesystem-safe opaque runtime identifier."""
    need(
        type(value) is str and RUNTIME_ID_RE.fullmatch(value) is not None,
        f"{field} is invalid",
    )
    return value


def exact_strings(values: tuple[str, ...], field: str, *, nonempty: bool = True) -> None:
    need(type(values) is tuple, f"{field} must be a tuple")
    need(all(type(value) is str and value.strip() for value in values), f"{field} has empty values")
    need(len(values) == len(set(values)), f"{field} has duplicates")
    if nonempty:
        need(bool(values), f"{field} is required")


def aware_timestamp(value: str, field: str) -> None:
    parsed = datetime.fromisoformat(value)
    need(parsed.tzinfo is not None and parsed.utcoffset() is not None, f"{field} needs UTC offset")


def compatible_version(version: str, requirement: str) -> bool:
    match = RANGE_RE.fullmatch(requirement)
    need(bool(VERSION_RE.fullmatch(version)) and match is not None, "version range is invalid")
    assert match is not None
    parsed = tuple(int(part) for part in version.split("."))
    lower = tuple(int(part) for part in match.group(1).split("."))
    upper = tuple(int(part) for part in match.group(2).split("."))
    return lower <= parsed < upper


@dataclass(frozen=True)
class ReleaseRef:
    name: str
    version: str
    archive: str
    sha256: str

    def validate(self) -> None:
        archive = PurePosixPath(self.archive)
        need(bool(IDENTITY_RE.fullmatch(self.name)), "release name is invalid")
        need(bool(VERSION_RE.fullmatch(self.version)), "release version must be semver")
        need(
            archive.suffix == ".zip"
            and not archive.is_absolute()
            and ".." not in archive.parts,
            "archive must be a safe relative ZIP path",
        )
        need(bool(HASH_RE.fullmatch(self.sha256)), "release sha256 is invalid")


@dataclass(frozen=True)
class StableManifest:
    schema_version: int
    channel: str
    skill: ReleaseRef
    company_pack: ReleaseRef
    compatible_skill: str
    published_by: str
    published_at: str
    force_update: bool

    def validate(self) -> None:
        need(self.schema_version == 1, "release manifest schema_version must be 1")
        need(self.channel in {item.value for item in ReleaseChannel}, "release channel is invalid")
        self.skill.validate()
        self.company_pack.validate()
        need(self.company_pack.version != "", "company pack version is required")
        need(compatible_version(self.skill.version, self.compatible_skill), "skill pair is incompatible")
        need(bool(self.published_by.strip()), "published_by is required")
        aware_timestamp(self.published_at, "published_at")
        need(type(self.force_update) is bool, "force_update must be boolean")


@dataclass(frozen=True)
class PackManifest:
    schema_version: int
    pack_id: str
    version: str
    compatible_skill: str
    published_by: str
    published_at: str
    runtime_paths: tuple[str, ...]

    def validate(self) -> None:
        need(self.schema_version == 1, "pack schema_version must be 1")
        need(bool(IDENTITY_RE.fullmatch(self.pack_id)), "pack_id is invalid")
        need(bool(VERSION_RE.fullmatch(self.version)), "pack version must be semver")
        need(RANGE_RE.fullmatch(self.compatible_skill) is not None, "compatible_skill is invalid")
        need(bool(self.published_by.strip()), "published_by is required")
        aware_timestamp(self.published_at, "published_at")
        exact_strings(self.runtime_paths, "runtime_paths")
        need(all(not path.startswith("/") and ".." not in path.split("/") for path in self.runtime_paths), "runtime path escapes pack")
        need(all(not path.startswith("governance") for path in self.runtime_paths), "governance is not distributable")


@dataclass(frozen=True)
class KnowledgeContract:
    stage: str
    required: tuple[str, ...]
    optional: tuple[str, ...]
    source_scopes: tuple[str, ...]
    authority: tuple[str, ...]
    max_items: int
    max_chars: int
    freshness_seconds: int
    missing_behavior: MissingBehavior
    evidence_required: bool
    writeback: str

    def validate(self) -> None:
        need(self.stage in {"discovery", "writer", "reviewer"}, "stage is invalid")
        exact_strings(self.required, "required")
        exact_strings(self.optional, "optional", nonempty=False)
        need(not set(self.required) & set(self.optional), "required and optional overlap")
        exact_strings(self.source_scopes, "source_scopes")
        exact_strings(self.authority, "authority")
        need(type(self.max_items) is int and 1 <= self.max_items <= 20, "max_items must be 1..20")
        need(type(self.max_chars) is int and 1000 <= self.max_chars <= 100000, "max_chars must be 1000..100000")
        need(type(self.freshness_seconds) is int and self.freshness_seconds >= 0, "freshness_seconds is invalid")
        need(type(self.missing_behavior) is MissingBehavior, "missing_behavior is invalid")
        need(type(self.evidence_required) is bool, "evidence_required must be boolean")
        need(self.writeback in {"none", "preview-confirm"}, "writeback is invalid")


@dataclass(frozen=True)
class WritePatch:
    document_id: str
    expected_revision: str
    command: WriteCommand
    content: str
    pattern: str | None
    block_id: str | None
    expected_fragment_sha256: str | None

    def validate(self) -> None:
        need(bool(self.document_id.strip()), "document_id is required")
        need(bool(self.expected_revision.strip()), "expected_revision is required")
        need(type(self.command) is WriteCommand, "write command is invalid")
        need(bool(self.content), "write content is required")
        if self.command is WriteCommand.STR_REPLACE:
            need(bool(self.pattern), "str_replace pattern is required")
            need(self.block_id is None and self.expected_fragment_sha256 is None, "str_replace has extra fields")
        else:
            need(bool(self.block_id), "block operation needs block_id")
            need(
                type(self.expected_fragment_sha256) is str
                and bool(HASH_RE.fullmatch(self.expected_fragment_sha256)),
                "block operation needs fragment sha256",
            )
            need(self.pattern is None, "block operation cannot have pattern")


@dataclass(frozen=True)
class WritePreview:
    document_id: str
    current_revision: str
    operation_sha256: str
    diff: str


@dataclass(frozen=True)
class WriteResult:
    document_id: str
    previous_revision: str
    new_revision: str
    updated_blocks_count: int


class RunStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    DRAFTING = "drafting"
    REVIEWING = "reviewing"
    REVISION_REQUIRED = "revision_required"
    PASSED = "passed"
    CONFLICT = "conflict"


class ReviewEvidence(StrEnum):
    PASS = "pass"
    BLOCK = "block"


class SpecReadiness(StrEnum):
    PASS = "pass"
    BLOCK = "block"
    NOT_ASSESSED = "not_assessed"


class ReviewSeverity(StrEnum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"


@dataclass(frozen=True)
class ReviewIssue:
    issue_id: str
    severity: ReviewSeverity
    title: str
    evidence_ref: str
    required_change: str

    def validate(self) -> None:
        need(
            type(self.issue_id) is str
            and bool(re.fullmatch(r"ISSUE-\d{3}", self.issue_id)),
            "issue_id is invalid",
        )
        need(type(self.severity) is ReviewSeverity, "severity is invalid")
        need(
            all(
                type(value) is str and value.strip()
                for value in (self.title, self.evidence_ref, self.required_change)
            ),
            "issue fields are required",
        )


@dataclass(frozen=True)
class ReviewReport:
    run_id: str
    review_context_id: str
    review_evidence: ReviewEvidence
    spec_readiness: SpecReadiness
    issues: tuple[ReviewIssue, ...]
    reviewed_artifact_sha256: str

    def validate(self) -> None:
        need(type(self.run_id) is str and bool(self.run_id.strip()), "run_id is required")
        need(
            type(self.review_context_id) is str
            and bool(self.review_context_id.strip()),
            "review_context_id is required",
        )
        need(type(self.review_evidence) is ReviewEvidence, "review_evidence is invalid")
        need(type(self.spec_readiness) is SpecReadiness, "spec_readiness is invalid")
        need(
            type(self.reviewed_artifact_sha256) is str
            and bool(HASH_RE.fullmatch(self.reviewed_artifact_sha256)),
            "reviewed artifact hash is invalid",
        )
        need(type(self.issues) is tuple, "issues must be a tuple")
        for issue in self.issues:
            need(type(issue) is ReviewIssue, "review issue is invalid")
            issue.validate()
        need(len({issue.issue_id for issue in self.issues}) == len(self.issues), "issue IDs must be unique")
        if self.review_evidence is ReviewEvidence.BLOCK:
            need(self.spec_readiness is SpecReadiness.NOT_ASSESSED, "evidence block requires not_assessed")


@dataclass(frozen=True)
class GateDecision:
    passed: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class WriterHandoff:
    schema_version: int
    run_id: str
    writer_context_id: str
    prd_path: str
    prd_sha256: str
    context_pack_sha256: str
    run_snapshot_path: str
    run_snapshot_sha256: str
    confirmed_decision_ids: tuple[str, ...]
    unresolved_decision_ids: tuple[str, ...]
    source_revisions: tuple[tuple[str, str], ...]

    def validate(self) -> None:
        need(self.schema_version == 1, "handoff schema_version must be 1")
        need(
            type(self.run_id) is str
            and type(self.writer_context_id) is str
            and bool(self.run_id.strip())
            and bool(self.writer_context_id.strip()),
            "handoff identity is required",
        )
        need(type(self.prd_path) is str and bool(self.prd_path.strip()), "prd_path is required")
        need(bool(HASH_RE.fullmatch(self.prd_sha256)), "prd_sha256 is invalid")
        need(bool(HASH_RE.fullmatch(self.context_pack_sha256)), "context_pack_sha256 is invalid")
        need(
            type(self.run_snapshot_path) is str and bool(self.run_snapshot_path.strip()),
            "run_snapshot_path is required",
        )
        need(bool(HASH_RE.fullmatch(self.run_snapshot_sha256)), "run_snapshot_sha256 is invalid")
        exact_strings(self.confirmed_decision_ids, "confirmed_decision_ids", nonempty=False)
        exact_strings(self.unresolved_decision_ids, "unresolved_decision_ids", nonempty=False)
        need(
            not set(self.confirmed_decision_ids) & set(self.unresolved_decision_ids),
            "confirmed and unresolved decisions overlap",
        )
        need(type(self.source_revisions) is tuple, "source_revisions must be a tuple")
        need(
            all(
                type(item) is tuple
                and len(item) == 2
                and all(type(value) is str and value.strip() for value in item)
                for item in self.source_revisions
            ),
            "source_revisions are invalid",
        )
        need(
            len({item[0] for item in self.source_revisions}) == len(self.source_revisions),
            "source revision IDs must be unique",
        )
