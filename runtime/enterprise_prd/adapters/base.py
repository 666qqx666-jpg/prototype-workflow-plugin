from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from enterprise_prd.contracts import AccessDecision
from enterprise_prd.product_knowledge.models import AdapterHeading, AdapterOutline, AdapterSection


class AccessAction(str, Enum):
    READ = "read"
    WRITE = "write"


@dataclass(frozen=True)
class SearchHit:
    document_id: str
    title: str
    revision: str
    source_url: str


@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    title: str
    revision: str
    content: str
    content_hash: str
    source_url: str
    realtime: bool


@dataclass(frozen=True)
class SourceMetadata:
    document_id: str
    title: str
    revision: str
    source_url: str
    document_type: str
    author: str | None
    modified_at: str | None


class DocumentAdapter(Protocol):
    def search(self, query: str, *, scope: str) -> tuple[SearchHit, ...]: ...
    def metadata(self, document_id: str) -> SourceMetadata: ...
    def read(self, document_id: str, *, revision: str | None = None) -> SourceDocument: ...
    def revision(self, document_id: str) -> str: ...
    def check_access(self, document_id: str, action: AccessAction) -> AccessDecision: ...


class ScopedReadAdapter(Protocol):
    def outline(self, document_id: str) -> AdapterOutline: ...
    def read_sections(self, document_id: str, *, revision: str, block_ids: tuple[str, ...]) -> tuple[AdapterSection, ...]: ...
