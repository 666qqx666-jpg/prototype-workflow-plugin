from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from enterprise_prd.errors import ContractError

from ..contracts import DirectoryEntry


_ROOT_FIELDS = {"schema_version", "entries"}
_ENTRY_FIELDS = {"locator_ref", "document_id"}


class PrivateLocatorResolver:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = dict(values)

    @classmethod
    def load(cls, path: Path) -> "PrivateLocatorResolver":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError("private locator data is unavailable or corrupt") from exc
        if (
            type(raw) is not dict
            or set(raw) != _ROOT_FIELDS
            or raw["schema_version"] != 1
            or type(raw["entries"]) is not list
            or not raw["entries"]
            or any(type(item) is not dict or set(item) != _ENTRY_FIELDS for item in raw["entries"])
        ):
            raise ContractError("private locator fields are invalid")
        refs = []
        document_ids = []
        for item in raw["entries"]:
            locator_ref = item["locator_ref"]
            document_id = item["document_id"]
            if (
                type(locator_ref) is not str
                or not locator_ref.startswith("local-secret:")
                or type(document_id) is not str
                or not document_id
                or any(character.isspace() for character in document_id)
            ):
                raise ContractError("private locator values are invalid")
            refs.append(locator_ref)
            document_ids.append(document_id)
        if len(refs) != len(set(refs)) or len(document_ids) != len(set(document_ids)):
            raise ContractError("private locator identities duplicate")
        return cls(dict(zip(refs, document_ids, strict=True)))

    def resolve(self, source: DirectoryEntry) -> str:
        source.validate()
        if source.adapter != "feishu" or not source.locator_ref.startswith("local-secret:"):
            raise ContractError(f"private locator is invalid for source: {source.source_id}")
        value = self._values.get(source.locator_ref)
        if value is None:
            raise ContractError(f"private locator is unavailable for source: {source.source_id}")
        return value


@dataclass(frozen=True)
class HeadingReference:
    source_id: str
    revision: str
    normalized_path: str
    block_id: str


class SessionHeadingIndex:
    def __init__(self, references: tuple[HeadingReference, ...]) -> None:
        if type(references) is not tuple or not references or any(
            type(item) is not HeadingReference for item in references
        ):
            raise ContractError("heading references are invalid")
        values: dict[tuple[str, str, str], str] = {}
        block_ids = []
        for item in references:
            if not all(
                type(value) is str and bool(value.strip())
                for value in (
                    item.source_id,
                    item.revision,
                    item.normalized_path,
                    item.block_id,
                )
            ):
                raise ContractError("heading reference values are invalid")
            key = (item.source_id, item.revision, item.normalized_path)
            if key in values:
                raise ContractError("heading reference identities duplicate")
            values[key] = item.block_id
            block_ids.append((item.source_id, item.revision, item.block_id))
        if len(block_ids) != len(set(block_ids)):
            raise ContractError("heading block identities duplicate")
        self._values = values

    @classmethod
    def empty(cls) -> "SessionHeadingIndex":
        value = cls.__new__(cls)
        value._values = {}
        return value

    def resolve(self, source_id: str, revision: str, normalized_path: str) -> str:
        block_id = self._values.get((source_id, revision, normalized_path))
        if block_id is None:
            raise ContractError(f"approved heading is unavailable for source: {source_id}")
        return block_id
