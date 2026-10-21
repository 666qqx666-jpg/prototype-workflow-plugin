from __future__ import annotations

import json
from pathlib import Path

from enterprise_prd.errors import AccessDenied, ContractError

from ..contracts import (
    AcquisitionScope,
    AdapterOutline,
    AdapterSection,
    ApprovedPath,
    DirectoryEntry,
    Heading,
)
from ..errors import SourceUnavailable


_SOURCE_FIELDS = {
    "source_id",
    "locator_ref",
    "document_id_sha256",
    "title",
    "revision",
    "acquisition_scopes",
    "headings",
    "sections",
}
_HEADING_FIELDS = {
    "block_id",
    "title",
    "normalized_title",
    "level",
    "raw_path",
    "normalized_path",
}


class FixtureSourceReader:
    def __init__(
        self,
        sources: dict[str, dict],
        *,
        denied: set[str] | None = None,
        unavailable: set[str] | None = None,
        revision_overrides: dict[str, str] | None = None,
    ) -> None:
        self.sources = sources
        self.denied = set(denied or ())
        self.unavailable = set(unavailable or ())
        self.revision_overrides = dict(revision_overrides or {})
        self.calls: list[tuple] = []

    @classmethod
    def load(cls, path: Path, **behavior) -> "FixtureSourceReader":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError("fixture sources are unavailable or corrupt") from exc
        if (
            type(raw) is not dict
            or set(raw) != {"schema_version", "sources"}
            or raw["schema_version"] != 1
            or type(raw["sources"]) is not list
        ):
            raise ContractError("fixture source fields are invalid")
        if not raw["sources"] or any(
            type(item) is not dict or set(item) != _SOURCE_FIELDS
            for item in raw["sources"]
        ):
            raise ContractError("fixture source item fields are invalid")
        if any(
            type(item["headings"]) is not list
            or not item["headings"]
            or any(
                type(heading) is not dict or set(heading) != _HEADING_FIELDS
                for heading in item["headings"]
            )
            for item in raw["sources"]
        ):
            raise ContractError("fixture heading fields are invalid")
        if any(
            type(item["sections"]) is not dict
            or any(
                type(section_path) is not str or type(content) is not str
                for section_path, content in item["sections"].items()
            )
            for item in raw["sources"]
        ):
            raise ContractError("fixture section fields are invalid")
        sources = {item["source_id"]: item for item in raw["sources"]}
        if len(sources) != len(raw["sources"]):
            raise ContractError("fixture source IDs duplicate")
        return cls(sources, **behavior)

    def _source(self, source: DirectoryEntry) -> dict:
        raw = self.sources.get(source.source_id)
        if (
            raw is None
            or raw["source_id"] != source.source_id
            or raw["locator_ref"] != source.locator_ref
        ):
            raise ContractError("fixture source does not match directory entry")
        return raw

    def get_outline(self, source: DirectoryEntry) -> AdapterOutline:
        self.calls.append(("get_outline", source.source_id))
        if source.source_id in self.denied:
            raise AccessDenied(f"access denied for source: {source.source_id}")
        if source.source_id in self.unavailable:
            raise SourceUnavailable(f"source unavailable: {source.source_id}")
        raw = self._source(source)
        try:
            return AdapterOutline(
                source.source_id,
                raw["document_id_sha256"],
                raw["title"],
                raw["revision"],
                tuple(Heading(**item) for item in raw["headings"]),
                tuple(
                    AcquisitionScope(item) for item in raw["acquisition_scopes"]
                ),
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise ContractError("fixture outline is invalid") from exc

    def read_sections(
        self,
        source: DirectoryEntry,
        revision: str,
        approved_paths: tuple[ApprovedPath, ...],
    ) -> tuple[AdapterSection, ...]:
        if (
            type(revision) is not str
            or not revision.strip()
            or type(approved_paths) is not tuple
            or not approved_paths
            or any(type(item) is not ApprovedPath for item in approved_paths)
        ):
            raise ContractError("fixture section request is invalid")
        self.calls.append(
            (
                "read_sections",
                source.source_id,
                tuple(
                    (item.normalized_path, item.acquisition_scope.value)
                    for item in approved_paths
                ),
            )
        )
        if source.source_id in self.denied:
            raise AccessDenied(f"access denied for source: {source.source_id}")
        if source.source_id in self.unavailable:
            raise SourceUnavailable(f"source unavailable: {source.source_id}")
        raw = self._source(source)
        if revision != raw["revision"]:
            raise ContractError("fixture requested revision is invalid")
        declared = set(raw["acquisition_scopes"])
        for item in approved_paths:
            if (
                type(item.normalized_path) is not str
                or not item.normalized_path.strip()
                or type(item.acquisition_scope) is not AcquisitionScope
                or item.acquisition_scope.value not in declared
                or item.normalized_path not in raw["sections"]
            ):
                raise ContractError("fixture approved path is invalid")
        revision_value = self.revision_overrides.get(source.source_id, raw["revision"])
        return tuple(
            AdapterSection(
                source.source_id,
                item.normalized_path,
                revision_value,
                item.acquisition_scope,
                raw["sections"][item.normalized_path],
            )
            for item in approved_paths
        )
