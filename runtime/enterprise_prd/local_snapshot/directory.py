from __future__ import annotations

import errno
import fcntl
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from enterprise_prd.canonical import canonical_json
from enterprise_prd.errors import AccessDenied, ContractError
from enterprise_prd.local_context.contracts import DirectoryEntry
from enterprise_prd.local_context.directory import YamlSourceDirectory
from enterprise_prd.local_context.errors import SourceUnavailable

from .errors import ConcurrentDirectoryConflict


@dataclass(frozen=True)
class DirectorySnapshot:
    canonical: bytes
    entries: tuple[DirectoryEntry, ...]


def _semantic(entries: tuple[DirectoryEntry, ...]) -> dict:
    return {"schema_version": 1, "entries": [{"source_id": item.source_id, "display_name": item.display_name, "adapter": item.adapter, "locator_ref": item.locator_ref} for item in sorted(entries, key=lambda item: item.source_id)]}


class GeneratedSnapshotDirectory:
    def __init__(self, private_root: Path) -> None:
        self.private_root = Path(private_root).resolve(strict=True)
        self.path = self.private_root / "local-snapshot-directory.yaml"

    def capture(self) -> DirectorySnapshot:
        try:
            if not self.path.exists():
                return DirectorySnapshot(b"ABSENT", ())
            if self.path.is_symlink() or not self.path.is_file():
                raise ContractError("generated snapshot directory is invalid")
            directory = YamlSourceDirectory.load(self.path)
            return DirectorySnapshot(canonical_json(_semantic(directory.entries)), directory.entries)
        except PermissionError as exc:
            raise AccessDenied("generated snapshot directory access is denied") from exc
        except ContractError:
            raise
        except OSError as exc:
            raise SourceUnavailable("generated snapshot directory is unavailable") from exc

    def _open_lock(self) -> int:
        private_descriptor = -1
        lock_directory_descriptor = -1
        lock_descriptor = -1
        directory_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_DIRECTORY", 0)
        try:
            private_descriptor = os.open(self.private_root, directory_flags)
            private_info = os.fstat(private_descriptor)
            if (
                not stat.S_ISDIR(private_info.st_mode)
                or private_info.st_mode & 0o777 != 0o700
            ):
                raise ContractError("generated snapshot private root is invalid")
            try:
                os.mkdir(".locks", mode=0o700, dir_fd=private_descriptor)
            except FileExistsError:
                pass
            lock_directory_descriptor = os.open(
                ".locks",
                directory_flags,
                dir_fd=private_descriptor,
            )
            lock_directory_info = os.fstat(lock_directory_descriptor)
            if (
                not stat.S_ISDIR(lock_directory_info.st_mode)
                or lock_directory_info.st_mode & 0o777 != 0o700
            ):
                raise ContractError("generated snapshot lock directory is invalid")
            fcntl.flock(lock_directory_descriptor, fcntl.LOCK_EX)
            try:
                try:
                    lock_descriptor = os.open(
                        "local-snapshot-directory.lock",
                        os.O_RDWR | os.O_NOFOLLOW,
                        dir_fd=lock_directory_descriptor,
                    )
                except FileNotFoundError:
                    try:
                        lock_descriptor = os.open(
                            "local-snapshot-directory.lock",
                            os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                            0o600,
                            dir_fd=lock_directory_descriptor,
                        )
                    except FileExistsError:
                        lock_descriptor = os.open(
                            "local-snapshot-directory.lock",
                            os.O_RDWR | os.O_NOFOLLOW,
                            dir_fd=lock_directory_descriptor,
                        )
            finally:
                fcntl.flock(lock_directory_descriptor, fcntl.LOCK_UN)
            lock_info = os.fstat(lock_descriptor)
            if (
                not stat.S_ISREG(lock_info.st_mode)
                or lock_info.st_mode & 0o777 != 0o600
            ):
                raise ContractError("generated snapshot lock file is invalid")
            result = lock_descriptor
            lock_descriptor = -1
            return result
        except PermissionError as exc:
            raise AccessDenied("generated snapshot directory access is denied") from exc
        except ContractError:
            raise
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ContractError("generated snapshot lock path is invalid") from exc
            raise SourceUnavailable("generated snapshot directory is unavailable") from exc
        finally:
            if lock_descriptor != -1:
                os.close(lock_descriptor)
            if lock_directory_descriptor != -1:
                os.close(lock_directory_descriptor)
            if private_descriptor != -1:
                os.close(private_descriptor)

    def publish(self, before: DirectorySnapshot, desired: DirectoryEntry) -> bool:
        desired.validate()
        descriptor = self._open_lock()
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            current = self.capture()
            current_entry = next((item for item in current.entries if item.source_id == desired.source_id), None)
            if current.canonical != before.canonical:
                if current_entry == desired:
                    return False
                raise ConcurrentDirectoryConflict("generated Directory changed during import")
            if current_entry == desired:
                return False
            entries = tuple(sorted(tuple(item for item in current.entries if item.source_id != desired.source_id) + (desired,), key=lambda item: item.source_id))
            if len({item.source_id for item in entries}) != len(entries) or len({(item.adapter, item.locator_ref) for item in entries}) != len(entries):
                raise ContractError("generated snapshot directory entries are invalid")
            for item in entries:
                item.validate()
            payload = yaml.safe_dump(_semantic(entries), allow_unicode=True, sort_keys=False, width=4096).encode("utf-8")
            temporary_name = None
            try:
                fd, temporary_name = tempfile.mkstemp(prefix=".local-snapshot-directory-", dir=self.private_root)
                os.fchmod(fd, 0o600)
                os.write(fd, payload)
                os.fsync(fd)
                os.close(fd)
                fd = -1
                YamlSourceDirectory.load(Path(temporary_name))
                os.replace(temporary_name, self.path)
                temporary_name = None
                parent_fd = os.open(self.private_root, os.O_RDONLY)
                try:
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
                return True
            finally:
                if temporary_name is not None:
                    try:
                        os.unlink(temporary_name)
                    except FileNotFoundError:
                        pass
        except PermissionError as exc:
            raise AccessDenied("generated snapshot directory access is denied") from exc
        except (AccessDenied, ContractError, ConcurrentDirectoryConflict, SourceUnavailable):
            raise
        except OSError as exc:
            raise SourceUnavailable("generated snapshot directory is unavailable") from exc
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
