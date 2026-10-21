from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

from enterprise_prd.baseline_registry.manifest import ManifestStore
from enterprise_prd.baseline_registry.snapshot import RegistrySnapshot
from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.local_context.adapters.fixture import FixtureSourceReader

from .bridge import TaskKnowledgeBridge
from .builder import build_manifest, build_preview, resolve_production_baseline
from .store import TaskKnowledgeStore
from .writer_input import build_writer_input_snapshot

FIXED_CLOCK = "2026-09-03T00:00:00Z"
FIXED_IDS = {"run_id": "RUN-TKMI-001", "session_id": "SESSION-TKMI-001", "grant_id": "GRANT-TKMI-001"}
FLOW_STEPS = ("preview", "baseline_persisted", "manifest", "outline", "role_sections", "grant", "context", "writer_input")


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 3, tzinfo=timezone.utc).astimezone(tz) if tz else datetime(2026, 9, 3)


def _business_hash(value: object, temporary_root: Path) -> str:
    def normalize(item):
        if type(item) is dict:
            return {key: normalize(value) for key, value in item.items() if not key.endswith("sha256") and key not in {"snapshot_id", "grant_id"}}
        if type(item) is list:
            return [normalize(value) for value in item]
        if type(item) is str:
            return item.replace(str(temporary_root), "/synthetic")
        return item
    return sha256_bytes(canonical_json(normalize(value)))


def run_fixture_flow(fixture_root: Path) -> dict:
    fixture_root = Path(fixture_root).resolve()
    with tempfile.TemporaryDirectory(prefix="task-knowledge-flow-", dir=fixture_root) as temporary:
        root = Path(temporary)
        snapshot = RegistrySnapshot.load_yaml(fixture_root / "registry.yaml")
        draft = yaml.safe_load((fixture_root / "draft.yaml").read_text())
        baseline = resolve_production_baseline(snapshot, task_label=draft["task"]["task_label"], surface_ids=tuple(draft["task"]["target_surface_ids"]), created_by="product-owner", created_at=FIXED_CLOCK)
        baseline_path = ManifestStore(root / "baseline").save(baseline)
        preview = build_preview(snapshot, baseline, draft)
        manifest = build_manifest(preview, {"schema_version": 1, "preview_payload_sha256": preview["preview_payload_sha256"], "confirmed_by": "product-owner", "confirmed_at": FIXED_CLOCK})
        manifest_path, _ = TaskKnowledgeStore(root / "task-knowledge").save_manifest(manifest)
        bridge = TaskKnowledgeBridge(root / "runtime", snapshot, manifest, {"fixture": FixtureSourceReader.load(fixture_root / "sources.json")}, grant_id_factory=lambda: FIXED_IDS["grant_id"])
        bridge.prepare_outlines(FIXED_IDS["session_id"])
        index = bridge.confirm_role_sections(FIXED_IDS["session_id"], yaml.safe_load((fixture_root / "sections.yaml").read_text())).role_section_index_path
        with patch("enterprise_prd.local_context.grant.datetime", _FixedDatetime):
            grant = bridge.issue_grant(FIXED_IDS["session_id"], index)
            context = bridge.assemble_context(grant, index)
        pair = root / "pair"; pack = pair / "pack"; pack.mkdir(parents=True); (pack / "fixture.txt").write_text("fixture")
        run = root / "run.json"; run.write_text(json.dumps({"schema_version": 1, "run_id": FIXED_IDS["run_id"], "status": "ready", "channel": "fixture", "skill_version": "0.2.0", "pack_version": "0.2.0", "manifest_sha256": "f" * 64, "pair_path": str(pair), "last_sync_at": FIXED_CLOCK, "started_at": FIXED_CLOCK}))
        writer = build_writer_input_snapshot(run_id=FIXED_IDS["run_id"], enterprise_snapshot_path=run, manifest_path=manifest_path, context_path=context, receipt_path=context.parent / "consumption.json", role_index_path=index, pack_path=pack, created_at=FIXED_CLOCK)
        TaskKnowledgeStore(root / "task-knowledge").save_writer_input(writer)
        objects = {"baseline": json.loads(baseline_path.read_text()), "preview": preview, "manifest": manifest, "index": json.loads(index.read_text()), "grant": json.loads(grant.read_text()), "context": json.loads(context.read_text()), "writer_input": writer}
        return {"status": "pass", "flow_steps": list(FLOW_STEPS), "object_hashes": {name: _business_hash(value, root) for name, value in objects.items()}, "source_count": len(objects["context"]["sources"]), "read_count": 2}
