from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from enterprise_prd.canonical import load_yaml
from enterprise_prd.errors import ContractError

from .contracts import DirectoryEntry


_ROOT_FIELDS = {"schema_version", "entries"}
_ENTRY_FIELDS = {"source_id", "display_name", "adapter", "locator_ref"}


@dataclass(frozen=True)
class YamlSourceDirectory:
    path: Path
    entries: tuple[DirectoryEntry, ...]

    @classmethod
    def load(cls, path: Path) -> "YamlSourceDirectory":
        resolved = path.expanduser().resolve()
        raw = load_yaml(resolved)
        if (
            type(raw) is not dict
            or set(raw) != _ROOT_FIELDS
            or raw["schema_version"] != 1
            or type(raw["entries"]) is not list
            or not raw["entries"]
        ):
            raise ContractError("source directory fields are invalid")
        entries = []
        for item in raw["entries"]:
            if type(item) is not dict or set(item) != _ENTRY_FIELDS:
                raise ContractError("source directory entry fields are invalid")
            value = DirectoryEntry(
                item["source_id"],
                item["display_name"],
                item["adapter"],
                item["locator_ref"],
            )
            value.validate()
            entries.append(value)
        if len({item.source_id for item in entries}) != len(entries):
            raise ContractError("source directory IDs must be unique")
        if len({(item.adapter, item.locator_ref) for item in entries}) != len(entries):
            raise ContractError("source directory locators must be unique")
        return cls(resolved, tuple(entries))

    def list_entries(self) -> tuple[DirectoryEntry, ...]:
        return self.entries

    def resolve(self, source_id: str) -> DirectoryEntry:
        matches = tuple(item for item in self.entries if item.source_id == source_id)
        if len(matches) != 1:
            raise ContractError(f"source is outside local directory: {source_id}")
        return matches[0]
