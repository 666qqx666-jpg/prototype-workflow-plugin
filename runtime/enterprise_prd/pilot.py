from __future__ import annotations

import os
from pathlib import Path
import tempfile

import yaml

from .adapters.base import AccessAction
from .contracts import AccessDecision
from .errors import ContractError
from .source_catalog import SourceTarget, load_source_catalog


def write_local_registry(targets: tuple[SourceTarget, ...], output: Path, pack_root: Path) -> None:
    if not targets:
        raise ContractError("at least one explicit source target is required")
    payload = {"schema_version": 2, "identity": "user", "sources": [
        {"source_id": target.source_id, "kind": target.kind,
         "locator": {"adapter": target.locator.adapter, "token": target.token},
         "classification": {"product_domain": target.classification.product_domain, "module": target.classification.module, "material_type": target.classification.material_type},
         "governance": {"lifecycle": target.governance.lifecycle.value, "superseded_by": list(target.governance.superseded_by)}}
        for target in targets]}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=output.parent,
        prefix=".catalog-local-",
    ) as raw:
        temporary = Path(raw) / output.name
        temporary.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        load_source_catalog(temporary, pack_root)
        os.replace(temporary, output)


def live_preflight(document_ids: tuple[str, ...], adapter) -> dict:
    if not document_ids: raise ContractError("live preflight requires explicit document IDs")
    accessible = denied = revisions = 0
    for document_id in document_ids:
        access = adapter.check_access(document_id, AccessAction.READ)
        if access is AccessDecision.DENIED: denied += 1; continue
        if access is not AccessDecision.ALLOWED: continue
        accessible += 1
        if adapter.metadata(document_id).revision: revisions += 1
    return {"status": "ready_for_scoped_live_preflight", "targets": len(document_ids), "accessible": accessible, "denied": denied, "revision_coverage": round(revisions / len(document_ids), 4)}
