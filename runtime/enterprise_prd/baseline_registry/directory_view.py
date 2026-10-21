from __future__ import annotations

from enterprise_prd.errors import ContractError

from .contracts import ObservationState
from .errors import BaselineResolutionError
from .manifest import validate_manifest
from .snapshot import RegistrySnapshot


def build_directory_view(snapshot: RegistrySnapshot, manifest: dict) -> dict:
    snapshot.validate()
    validate_manifest(manifest)
    if manifest["registry_view_hash"] != snapshot.view_hash:
        raise BaselineResolutionError("registry_view_changed")
    entries = []
    for item in manifest["documents"]:
        document = snapshot.document(item["source_id"])
        revision = snapshot.revision(item["source_id"], item["revision"])
        if revision.observation_state is not ObservationState.AVAILABLE:
            raise BaselineResolutionError("source_unavailable")
        entries.append({
            "source_id": document.source_id,
            "display_name": document.display_name,
            "adapter": document.adapter,
            "locator_ref": document.locator_ref,
            "expected_revision": item["revision"],
            "baseline_role": item["role"],
        })
    if [item["source_id"] for item in entries] != sorted(item["source_id"] for item in entries):
        raise ContractError("directory view is not canonical")
    return {
        "schema_version": 1,
        "manifest_id": manifest["manifest_id"],
        "entries": entries,
    }
