from __future__ import annotations

import json, os
from datetime import datetime, timezone
from pathlib import Path

from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.errors import ContractError, IntegrityError
from .models import ProjectionInvalidationReason, ProjectionStatus, SourceObservation


def _now() -> str: return datetime.now(timezone.utc).isoformat(timespec="seconds")
def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_name(f".{path.name}.new")
    try: temporary.write_bytes(data); os.replace(temporary, path)
    finally: temporary.unlink(missing_ok=True)


class ProductKnowledgeStore:
    def __init__(self, root: Path) -> None: self.root = root.resolve()

    def observe(self, value: SourceObservation) -> Path:
        payload = {"schema_version": 1, "source_id": value.source_id, "adapter": value.adapter, "document_id_sha256": value.document_id_sha256, "title": value.title, "revision": value.revision, "modified_at": value.modified_at, "access": value.access.value, "observed_at": value.observed_at}
        digest = sha256_bytes(canonical_json(payload)); directory = self.root / "observations" / value.source_id; directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{len(list(directory.glob('*.json'))) + 1:06d}-{digest}.json"; _atomic(path, canonical_json({**payload, "event_sha256": digest})); return path

    def write_projection(self, payload: dict) -> Path:
        required = {"schema_version", "projection_type", "extractor_version", "registry_sha256", "source_id", "revision", "generated_at"}
        if not required <= set(payload) or "content" in payload or "token" in payload: raise ContractError("projection payload is invalid")
        digest = sha256_bytes(canonical_json(payload)); path = self.root / "projections" / payload["source_id"] / str(payload["revision"]) / f"{payload['projection_type']}-{digest}.json"; data = canonical_json({**payload, "projection_sha256": digest})
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != data: raise IntegrityError("projection bytes conflict")
            return path
        try:
            with path.open("xb") as handle: handle.write(data)
        except FileExistsError:
            if path.read_bytes() != data: raise IntegrityError("projection bytes conflict")
        return path

    def invalidate_source_revision(self, source_id: str, *, locked_revision: str, observed_revision: str, reason: ProjectionInvalidationReason = ProjectionInvalidationReason.REVISION_CHANGED) -> tuple[Path, ...]:
        root = self.root / "projections" / source_id / locked_revision
        result = []
        for projection in root.glob("*.json") if root.exists() else ():
            data = json.loads(projection.read_text()) ; digest = data.get("projection_sha256")
            if not digest: continue
            directory = self.root / "projection-events" / digest; directory.mkdir(parents=True, exist_ok=True)
            if any(json.loads(path.read_text()).get("reason") == reason.value for path in directory.glob("*.json")): continue
            payload = {"schema_version": 1, "projection_sha256": digest, "reason": reason.value, "observed_revision": observed_revision, "invalidated_at": _now()}; event = sha256_bytes(canonical_json(payload)); path = directory / f"{len(list(directory.glob('*.json'))) + 1:06d}-{event}.json"; _atomic(path, canonical_json({**payload, "event_sha256": event})); result.append(path)
        return tuple(result)
