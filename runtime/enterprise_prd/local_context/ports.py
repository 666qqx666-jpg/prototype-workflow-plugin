from __future__ import annotations

from typing import Protocol

from .contracts import AdapterOutline, AdapterSection, ApprovedPath, DirectoryEntry


class SourceDirectoryPort(Protocol):
    def list_entries(self) -> tuple[DirectoryEntry, ...]: ...

    def resolve(self, source_id: str) -> DirectoryEntry: ...


class SourceReaderPort(Protocol):
    def get_outline(self, source: DirectoryEntry) -> AdapterOutline: ...

    def read_sections(
        self,
        source: DirectoryEntry,
        revision: str,
        approved_paths: tuple[ApprovedPath, ...],
    ) -> tuple[AdapterSection, ...]: ...
