from __future__ import annotations

import re
from dataclasses import dataclass

from enterprise_prd.contracts import IDENTITY_RE, StrEnum, need
from enterprise_prd.heading_selection import normalize_heading


LOCATOR_REF_RE = re.compile(
    r"^[a-z][a-z0-9-]{0,31}:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)


class SessionStatus(StrEnum):
    CREATED = "created"
    DOCUMENTS_CONFIRMED = "documents_confirmed"
    OUTLINES_READY = "outlines_ready"
    SECTIONS_CONFIRMED = "sections_confirmed"
    GRANT_READY = "grant_ready"
    CONSUMED = "consumed"
    ACCESS_DENIED = "access_denied"
    REVISION_CHANGED = "revision_changed"
    INVALID_SOURCE = "invalid_source"
    INVALID_OUTLINE = "invalid_outline"
    CANCELLED = "cancelled"


class AcquisitionScope(StrEnum):
    NATIVE_SECTIONS = "native_sections"
    FULL_DOCUMENT_LOCAL_EXTRACT = "full_document_local_extract"


@dataclass(frozen=True)
class DirectoryEntry:
    source_id: str
    display_name: str
    adapter: str
    locator_ref: str

    def validate(self) -> None:
        need(
            type(self.source_id) is str
            and IDENTITY_RE.fullmatch(self.source_id) is not None,
            "source_id is invalid",
        )
        need(
            type(self.display_name) is str and bool(self.display_name.strip()),
            "display_name is invalid",
        )
        need(
            type(self.adapter) is str and IDENTITY_RE.fullmatch(self.adapter) is not None,
            "adapter is invalid",
        )
        need(
            type(self.locator_ref) is str
            and LOCATOR_REF_RE.fullmatch(self.locator_ref) is not None,
            "locator_ref is invalid",
        )


@dataclass(frozen=True)
class Candidate:
    entry: DirectoryEntry
    purpose: str

    def validate(self) -> None:
        self.entry.validate()
        need(
            type(self.purpose) is str and bool(self.purpose.strip()),
            "candidate purpose is invalid",
        )


@dataclass(frozen=True)
class Heading:
    block_id: str
    title: str
    normalized_title: str
    level: int
    raw_path: str
    normalized_path: str

    def validate(self) -> None:
        need(
            all(
                type(value) is str and bool(value.strip())
                for value in (
                    self.block_id,
                    self.title,
                    self.normalized_title,
                    self.raw_path,
                    self.normalized_path,
                )
            ),
            "heading strings are invalid",
        )
        need(type(self.level) is int and 1 <= self.level <= 6, "heading level is invalid")
        raw_parts = self.raw_path.split("/")
        need(all(part.strip() for part in raw_parts), "heading path contains an empty component")
        need(
            self.normalized_title == normalize_heading(self.title),
            "normalized heading title is invalid",
        )
        need(
            self.normalized_path
            == "/".join(normalize_heading(part) for part in raw_parts),
            "normalized heading path is invalid",
        )


@dataclass(frozen=True)
class AdapterOutline:
    source_id: str
    document_id_sha256: str
    title: str
    revision: str
    headings: tuple[Heading, ...]
    acquisition_scopes: tuple[AcquisitionScope, ...]


@dataclass(frozen=True)
class RequestedSection:
    source_id: str
    raw_path: str
    acquisition_scope: AcquisitionScope


@dataclass(frozen=True)
class ApprovedPath:
    normalized_path: str
    acquisition_scope: AcquisitionScope


@dataclass(frozen=True)
class SelectedSection:
    source_id: str
    purpose: str
    block_id: str
    raw_path: str
    normalized_path: str
    heading_level: int
    acquisition_scope: AcquisitionScope


@dataclass(frozen=True)
class AdapterSection:
    source_id: str
    normalized_path: str
    revision: str
    acquisition_scope: AcquisitionScope
    content: str
