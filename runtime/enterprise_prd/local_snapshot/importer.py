from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from enterprise_prd.errors import ContractError
from enterprise_prd.local_context.contracts import DirectoryEntry

from .directory import GeneratedSnapshotDirectory
from .errors import InputChangedDuringImport
from .markdown import parse_markdown
from .snapshot import SnapshotStore, build_manifest, validate_private_root

MAX_MARKDOWN_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class ImportResult:
    source_id: str
    revision: str
    snapshot_ref_sha256: str
    locator_ref: str
    content_bytes: int
    heading_count: int
    bundle_created: bool
    directory_updated: bool


def read_explicit_markdown(path: Path) -> bytes:
    value = Path(path)
    if not value.is_absolute() or value.suffix != ".md" or value.name != value.name or value.is_symlink():
        raise ContractError("snapshot input is invalid")
    try:
        descriptor = os.open(value, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ContractError("snapshot input is invalid") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
            raise ContractError("snapshot input is invalid")
        chunks: list[bytes] = []
        remaining = MAX_MARKDOWN_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(payload) > MAX_MARKDOWN_BYTES:
        raise ContractError("snapshot input is invalid")
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise InputChangedDuringImport("input changed during import")
    try:
        decoded = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError("snapshot input encoding is invalid") from exc
    if "\x00" in decoded or "\ufffd" in decoded:
        raise ContractError("snapshot input is invalid")
    return decoded.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


class SnapshotImporter:
    def __init__(self, private_root: Path) -> None:
        self.private_root = validate_private_root(private_root)
        self.store = SnapshotStore(self.private_root / "snapshots")
        self.directory = GeneratedSnapshotDirectory(self.private_root)

    def import_markdown(self, path: Path, source_id: str, display_name: str) -> ImportResult:
        before = self.directory.capture()
        document = read_explicit_markdown(path)
        parsed = parse_markdown(document.decode("utf-8"))
        manifest = build_manifest(source_id, document)
        created = self.store.commit(manifest, document)
        entry = DirectoryEntry(source_id, display_name, "local-snapshot", f"snapshot:{manifest['snapshot_ref_sha256']}")
        entry.validate()
        updated = self.directory.publish(before, entry)
        return ImportResult(source_id, manifest["content_sha256"], manifest["snapshot_ref_sha256"], entry.locator_ref, manifest["content_bytes"], len(parsed.headings), created, updated)
