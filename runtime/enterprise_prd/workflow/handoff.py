from __future__ import annotations

import json
from pathlib import Path

from enterprise_prd.canonical import sha256_file
from enterprise_prd.contracts import WriterHandoff
from enterprise_prd.errors import ContractError, IntegrityError


HANDOFF_KEYS = {
    "schema_version", "run_id", "writer_context_id", "prd_path",
    "prd_sha256", "context_pack_sha256", "run_snapshot_path", "run_snapshot_sha256",
    "confirmed_decision_ids", "unresolved_decision_ids", "source_revisions",
}


def _payload(handoff: WriterHandoff) -> dict:
    return {
        "schema_version": handoff.schema_version,
        "run_id": handoff.run_id,
        "writer_context_id": handoff.writer_context_id,
        "prd_path": handoff.prd_path,
        "prd_sha256": handoff.prd_sha256,
        "context_pack_sha256": handoff.context_pack_sha256,
        "run_snapshot_path": handoff.run_snapshot_path,
        "run_snapshot_sha256": handoff.run_snapshot_sha256,
        "confirmed_decision_ids": list(handoff.confirmed_decision_ids),
        "unresolved_decision_ids": list(handoff.unresolved_decision_ids),
        "source_revisions": [list(item) for item in handoff.source_revisions],
    }


def create_handoff(
    *,
    run_id: str,
    writer_context_id: str,
    prd_path: Path,
    context_pack_sha256: str,
    run_snapshot_path: Path,
    confirmed_decision_ids: tuple[str, ...],
    unresolved_decision_ids: tuple[str, ...],
    source_revisions: tuple[tuple[str, str], ...],
    output: Path,
) -> WriterHandoff:
    handoff = WriterHandoff(
        schema_version=1,
        run_id=run_id,
        writer_context_id=writer_context_id,
        prd_path=str(prd_path.resolve()),
        prd_sha256=sha256_file(prd_path),
        context_pack_sha256=context_pack_sha256,
        run_snapshot_path=str(run_snapshot_path.resolve()),
        run_snapshot_sha256=sha256_file(run_snapshot_path),
        confirmed_decision_ids=confirmed_decision_ids,
        unresolved_decision_ids=unresolved_decision_ids,
        source_revisions=source_revisions,
    )
    handoff.validate()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(_payload(handoff), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return handoff


def load_for_reviewer(path: Path, review_context_id: str) -> WriterHandoff:
    if type(review_context_id) is not str or not review_context_id.strip():
        raise ContractError("review_context_id is required")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("Writer handoff is not valid JSON") from exc
    if type(raw) is not dict or set(raw) != HANDOFF_KEYS:
        raise ContractError("Writer handoff fields are invalid")
    if (
        type(raw["confirmed_decision_ids"]) is not list
        or type(raw["unresolved_decision_ids"]) is not list
        or type(raw["source_revisions"]) is not list
        or any(type(item) is not list for item in raw["source_revisions"])
    ):
        raise ContractError("Writer handoff collection fields are invalid")
    handoff = WriterHandoff(
        schema_version=raw["schema_version"],
        run_id=raw["run_id"],
        writer_context_id=raw["writer_context_id"],
        prd_path=raw["prd_path"],
        prd_sha256=raw["prd_sha256"],
        context_pack_sha256=raw["context_pack_sha256"],
        run_snapshot_path=raw["run_snapshot_path"],
        run_snapshot_sha256=raw["run_snapshot_sha256"],
        confirmed_decision_ids=tuple(raw["confirmed_decision_ids"]),
        unresolved_decision_ids=tuple(raw["unresolved_decision_ids"]),
        source_revisions=tuple(tuple(item) for item in raw["source_revisions"]),
    )
    handoff.validate()
    if review_context_id == handoff.writer_context_id:
        raise ContractError("Reviewer must use a fresh context ID")
    if sha256_file(Path(handoff.prd_path)) != handoff.prd_sha256:
        raise IntegrityError("PRD changed after Writer handoff")
    if sha256_file(Path(handoff.run_snapshot_path)) != handoff.run_snapshot_sha256:
        raise IntegrityError("run snapshot changed after Writer handoff")
    try:
        snapshot = json.loads(
            Path(handoff.run_snapshot_path).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError("run snapshot is not valid JSON") from exc
    if type(snapshot) is not dict or snapshot.get("run_id") != handoff.run_id:
        raise IntegrityError("run snapshot identity does not match Writer handoff")
    return handoff
