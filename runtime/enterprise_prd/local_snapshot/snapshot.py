from __future__ import annotations

import json
import fcntl
import errno
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.contracts import HASH_RE, IDENTITY_RE
from enterprise_prd.errors import AccessDenied, ContractError
from enterprise_prd.local_context.contracts import DirectoryEntry
from enterprise_prd.local_context.errors import SourceUnavailable

from .markdown import ParsedMarkdown, parse_markdown

MANIFEST_FIELDS = {"schema_version", "snapshot_format", "source_id", "content_file", "content_sha256", "snapshot_ref_sha256", "content_bytes", "encoding", "newline", "acquisition_scope"}
_LOCATOR_PREFIX = "snapshot:"


@dataclass(frozen=True)
class SnapshotRecord:
    root: Path
    manifest: dict
    document: str
    parsed: ParsedMarkdown


def _valid_source_id(source_id: object) -> str:
    if type(source_id) is not str or IDENTITY_RE.fullmatch(source_id) is None:
        raise ContractError("snapshot source ID is invalid")
    return source_id


def snapshot_ref_sha256(source_id: str, content_sha256: str) -> str:
    _valid_source_id(source_id)
    if type(content_sha256) is not str or HASH_RE.fullmatch(content_sha256) is None:
        raise ContractError("snapshot content hash is invalid")
    return sha256_bytes(b"local-snapshot-ref-v1\0" + source_id.encode() + b"\0" + content_sha256.encode())


def document_identity_sha256(source_id: str) -> str:
    _valid_source_id(source_id)
    return sha256_bytes(b"local-snapshot-source-v1\0" + source_id.encode())


def heading_block_id(source_id: str, revision: str, normalized_path: str) -> str:
    _valid_source_id(source_id)
    if type(revision) is not str or HASH_RE.fullmatch(revision) is None or type(normalized_path) is not str or not normalized_path.strip():
        raise ContractError("snapshot heading identity is invalid")
    digest = sha256_bytes(b"local-snapshot-heading-v1\0" + source_id.encode() + b"\0" + revision.encode() + b"\0" + normalized_path.encode())
    return f"snapshot-{digest[:32]}"


def build_manifest(source_id: str, document: bytes) -> dict:
    _valid_source_id(source_id)
    if type(document) is not bytes or not document:
        raise ContractError("snapshot document is invalid")
    try:
        text = document.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError("snapshot document encoding is invalid") from exc
    if "\r" in text:
        raise ContractError("snapshot document newline is invalid")
    parse_markdown(text)
    content_hash = sha256_bytes(document)
    return {"schema_version": 1, "snapshot_format": "local-markdown-v1", "source_id": source_id, "content_file": "document.md", "content_sha256": content_hash, "snapshot_ref_sha256": snapshot_ref_sha256(source_id, content_hash), "content_bytes": len(document), "encoding": "utf-8", "newline": "lf", "acquisition_scope": "full_document_local_extract"}


def validate_private_root(private_root: Path) -> Path:
    path = Path(private_root)
    if path.is_symlink() or not path.is_dir():
        raise ContractError("snapshot private root is invalid")
    resolved = path.resolve(strict=True)
    if resolved.is_symlink() or resolved.stat().st_mode & 0o777 != 0o700:
        raise ContractError("snapshot private root is invalid")
    return resolved


class SnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _root(self) -> Path:
        if self.root.exists():
            if self.root.is_symlink() or not self.root.is_dir():
                raise ContractError("snapshot store root is invalid")
        else:
            try:
                self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
            except PermissionError as exc:
                raise AccessDenied("snapshot store access is denied") from exc
            except OSError as exc:
                raise SourceUnavailable("snapshot store is unavailable") from exc
        os.chmod(self.root, 0o700)
        return self.root.resolve(strict=True)

    def _read_root(self) -> Path:
        try:
            if not os.path.lexists(self.root):
                raise SourceUnavailable("snapshot is unavailable")
            info = os.lstat(self.root)
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
                or info.st_mode & 0o777 != 0o700
            ):
                raise ContractError("snapshot store root is invalid")
            return self.root.resolve(strict=True)
        except PermissionError as exc:
            raise AccessDenied("snapshot access is denied") from exc
        except (AccessDenied, ContractError, SourceUnavailable):
            raise
        except FileNotFoundError as exc:
            raise SourceUnavailable("snapshot is unavailable") from exc
        except OSError as exc:
            raise SourceUnavailable("snapshot is unavailable") from exc

    def _source_root(self, source_id: str) -> Path:
        root = self._root() / _valid_source_id(source_id)
        try:
            root.mkdir(mode=0o700, exist_ok=True)
            if root.is_symlink() or not root.is_dir():
                raise ContractError("snapshot source directory is invalid")
            os.chmod(root, 0o700)
            return root
        except PermissionError as exc:
            raise AccessDenied("snapshot store access is denied") from exc
        except ContractError:
            raise
        except OSError as exc:
            raise SourceUnavailable("snapshot store is unavailable") from exc

    def _fsync_directory(self, path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write_private(self, path: Path, payload: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _read_private(self, path: Path, maximum_bytes: int) -> bytes:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except PermissionError as exc:
            raise AccessDenied("snapshot access is denied") from exc
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR} or path.is_symlink():
                raise ContractError("snapshot bundle is invalid") from exc
            if exc.errno == errno.ENOENT:
                raise SourceUnavailable("snapshot is unavailable") from exc
            raise SourceUnavailable("snapshot is unavailable") from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o777 != 0o600 or info.st_size > maximum_bytes:
                raise ContractError("snapshot private file is invalid")
            payload = b""
            while len(payload) <= maximum_bytes:
                chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - len(payload)))
                if not chunk:
                    break
                payload += chunk
            if len(payload) > maximum_bytes:
                raise ContractError("snapshot private file is invalid")
            return payload
        finally:
            os.close(descriptor)

    def _remove_owned_temporary(self, path: Path, expected_parent: Path) -> None:
        if path.parent != expected_parent or not path.name.startswith(".snapshot-") or path.is_symlink():
            raise ContractError("snapshot temporary path is invalid")
        shutil.rmtree(path)

    def commit(self, manifest: dict, document: bytes) -> bool:
        expected = build_manifest(manifest.get("source_id") if type(manifest) is dict else None, document)
        if manifest != expected:
            raise ContractError("snapshot manifest is invalid")
        parent = self._source_root(expected["source_id"])
        target = parent / expected["snapshot_ref_sha256"]
        lock_fd = os.open(parent / ".snapshot-store.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            return self._commit_locked(parent, target, expected, document)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def _commit_locked(self, parent: Path, target: Path, expected: dict, document: bytes) -> bool:
        if target.exists():
            entry = DirectoryEntry(expected["source_id"], "snapshot", "local-snapshot", _LOCATOR_PREFIX + expected["snapshot_ref_sha256"])
            record = self._validate_record(target, entry)
            if record.manifest != expected or record.document.encode() != document:
                raise ContractError("snapshot immutable bundle conflicts")
            return False
        temporary = parent / f".snapshot-{uuid.uuid4().hex}"
        try:
            temporary.mkdir(mode=0o700)
            self._write_private(temporary / "manifest.json", canonical_json(expected))
            self._write_private(temporary / "document.md", document)
            self._fsync_directory(temporary)
            try:
                os.rename(temporary, target)
            except FileExistsError:
                self._remove_owned_temporary(temporary, parent)
                entry = DirectoryEntry(expected["source_id"], "snapshot", "local-snapshot", _LOCATOR_PREFIX + expected["snapshot_ref_sha256"])
                record = self._validate_record(target, entry)
                if record.manifest != expected or record.document.encode() != document:
                    raise ContractError("snapshot immutable bundle conflicts")
                return False
            self._fsync_directory(parent)
            return True
        except PermissionError as exc:
            if temporary.exists():
                self._remove_owned_temporary(temporary, parent)
            raise AccessDenied("snapshot store access is denied") from exc
        except (AccessDenied, ContractError, SourceUnavailable):
            raise
        except OSError as exc:
            if temporary.exists():
                self._remove_owned_temporary(temporary, parent)
            raise SourceUnavailable("snapshot store is unavailable") from exc

    def _validate_record(self, root: Path, entry: DirectoryEntry) -> SnapshotRecord:
        entry.validate()
        if entry.adapter != "local-snapshot" or not entry.locator_ref.startswith(_LOCATOR_PREFIX):
            raise ContractError("snapshot directory entry is invalid")
        reference = entry.locator_ref.removeprefix(_LOCATOR_PREFIX)
        if HASH_RE.fullmatch(reference) is None or root.name != reference or root.parent.name != entry.source_id:
            raise ContractError("snapshot location is invalid")
        if not os.path.lexists(root):
            raise SourceUnavailable("snapshot is unavailable")
        if root.is_symlink() or not root.is_dir() or root.stat().st_mode & 0o777 != 0o700:
            raise ContractError("snapshot bundle is invalid")
        try:
            if {child.name for child in root.iterdir()} != {"manifest.json", "document.md"}:
                raise ContractError("snapshot bundle members are invalid")
        except PermissionError as exc:
            raise AccessDenied("snapshot access is denied") from exc
        except OSError as exc:
            raise SourceUnavailable("snapshot is unavailable") from exc
        try:
            manifest = json.loads(self._read_private(root / "manifest.json", 64 * 1024).decode("utf-8"))
            document_bytes = self._read_private(root / "document.md", 5 * 1024 * 1024)
            document = document_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractError("snapshot encoding is invalid") from exc
        except json.JSONDecodeError as exc:
            raise ContractError("snapshot manifest is invalid") from exc
        if type(manifest) is not dict or set(manifest) != MANIFEST_FIELDS:
            raise ContractError("snapshot manifest fields are invalid")
        expected = build_manifest(entry.source_id, document_bytes)
        if manifest != expected or manifest["snapshot_ref_sha256"] != reference:
            raise ContractError("snapshot manifest does not match bundle")
        return SnapshotRecord(root, manifest, document, parse_markdown(document))

    def load(self, entry: DirectoryEntry) -> SnapshotRecord:
        entry.validate()
        if entry.adapter != "local-snapshot" or not entry.locator_ref.startswith(_LOCATOR_PREFIX):
            raise ContractError("snapshot directory entry is invalid")
        reference = entry.locator_ref.removeprefix(_LOCATOR_PREFIX)
        if HASH_RE.fullmatch(reference) is None:
            raise ContractError("snapshot locator is invalid")
        root = self._read_root() / entry.source_id / reference
        return self._validate_record(root, entry)
