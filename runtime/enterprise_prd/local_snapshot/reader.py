from __future__ import annotations

from enterprise_prd.contracts import HASH_RE
from enterprise_prd.errors import ContractError
from enterprise_prd.heading_selection import normalize_heading
from enterprise_prd.local_context.contracts import AcquisitionScope, AdapterOutline, AdapterSection, ApprovedPath, DirectoryEntry, Heading

from .snapshot import SnapshotStore, document_identity_sha256, heading_block_id


class LocalSnapshotReader:
    def __init__(self, store_root):
        self.store = SnapshotStore(store_root)

    def get_outline(self, source: DirectoryEntry) -> AdapterOutline:
        record = self.store.load(source)
        revision = record.manifest["content_sha256"]
        headings = tuple(Heading(heading_block_id(source.source_id, revision, item.normalized_path), item.title, item.normalized_title, item.level, item.raw_path, item.normalized_path) for item in record.parsed.headings)
        for heading in headings: heading.validate()
        if len({item.block_id for item in headings}) != len(headings): raise ContractError("snapshot heading block IDs must be unique")
        return AdapterOutline(source.source_id, document_identity_sha256(source.source_id), record.parsed.title, revision, headings, (AcquisitionScope.FULL_DOCUMENT_LOCAL_EXTRACT,))

    def read_sections(self, source: DirectoryEntry, revision: str, approved_paths: tuple[ApprovedPath, ...]) -> tuple[AdapterSection, ...]:
        if type(revision) is not str or HASH_RE.fullmatch(revision) is None or type(approved_paths) is not tuple or not approved_paths or any(type(item) is not ApprovedPath for item in approved_paths):
            raise ContractError("snapshot section request is invalid")
        normalized_paths = tuple(item.normalized_path for item in approved_paths)
        if len(normalized_paths) != len(set(normalized_paths)) or any(type(item.normalized_path) is not str or not item.normalized_path.strip() or "/".join(normalize_heading(part) for part in item.normalized_path.split("/")) != item.normalized_path or item.acquisition_scope is not AcquisitionScope.FULL_DOCUMENT_LOCAL_EXTRACT for item in approved_paths):
            raise ContractError("snapshot approved paths are invalid")
        record = self.store.load(source)
        current = record.manifest["content_sha256"]
        if current != revision: raise ContractError("snapshot requested revision is invalid")
        contents = record.parsed.extract(normalized_paths)
        return tuple(AdapterSection(source.source_id, item.normalized_path, current, item.acquisition_scope, content) for item, content in zip(approved_paths, contents, strict=True))
