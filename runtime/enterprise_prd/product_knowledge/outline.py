from __future__ import annotations

from datetime import datetime, timezone

from enterprise_prd.adapters.base import AccessAction
from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.errors import AccessDenied, ContractError
from enterprise_prd.heading_selection import normalize_heading
from .models import AcquisitionScope, AdapterHeading, ContextBinding, RequestedSection, SectionSelection, SelectionState, SelectionStatus, SourceObservation, SourceOutline
from .selection import SelectionService
from .stores import ProductKnowledgeStore
from enterprise_prd.contracts import AccessDecision


class OutlineWorkflow:
    def __init__(self, selection: SelectionService, store: ProductKnowledgeStore, registry, adapters: dict) -> None:
        self.selection, self.store, self.registry, self.adapters = selection, store, registry, adapters

    def preflight(self, session_id: str) -> tuple[SourceOutline, ...]:
        state = self.selection.load(session_id)
        if state.status is not SelectionStatus.DOCUMENTS_CONFIRMED:
            raise ContractError("documents must be confirmed before outline")
        outlines = []
        missing_optional = []
        confirmed_by_source: dict[str, list] = {}
        for confirmed in state.confirmed_documents:
            confirmed_by_source.setdefault(confirmed.source_id, []).append(confirmed)
        for source_id, confirmed_items in confirmed_by_source.items():
            target = self.registry.resolve(source_id)
            adapter = self.adapters[target.locator.adapter]
            if adapter.check_access(target.token, AccessAction.READ) is not AccessDecision.ALLOWED:
                if any(item.required for item in confirmed_items):
                    self.selection.fail(session_id, SelectionStatus.ACCESS_DENIED, "source access denied")
                    raise AccessDenied("source access denied")
                missing_optional.extend(item.binding for item in confirmed_items)
                continue
            value = adapter.outline(target.token)
            if value.document_id != target.token:
                raise ContractError("outline identity mismatch")
            payload = {"schema_version": 1, "projection_type": "document-outline", "extractor_version": "outline-v1", "registry_sha256": state.registry_sha256, "source_id": target.source_id, "document_id_sha256": sha256_bytes(target.token.encode()), "title": value.title, "revision": value.revision, "adapter": target.locator.adapter, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "headings": [heading.__dict__ for heading in value.headings]}
            digest = sha256_bytes(canonical_json(payload))
            outline = SourceOutline(1, "document-outline", "outline-v1", state.registry_sha256, target.source_id, payload["document_id_sha256"], value.title, value.revision, target.locator.adapter, payload["generated_at"], value.headings, digest)
            self.store.observe(SourceObservation(target.source_id, target.locator.adapter, payload["document_id_sha256"], value.title, value.revision, None, AccessDecision.ALLOWED, payload["generated_at"]))
            self.store.write_projection(payload)
            outlines.append(outline)
        self.selection.record_outlines(
            session_id,
            tuple(outlines),
            tuple(dict.fromkeys(missing_optional)),
        )
        return tuple(outlines)

    def confirm_sections(self, session_id: str, requested: tuple[RequestedSection, ...], scope: AcquisitionScope) -> SelectionState:
        state = self.selection.load(session_id)
        if state.status is not SelectionStatus.OUTLINES_READY or not requested:
            raise ContractError("outlines must be ready before sections")
        by_source = {item.source_id: item for item in state.outlines}
        selections = []
        for item in requested:
            outline = by_source.get(item.source_id)
            if outline is None:
                raise ContractError("section source is not confirmed")
            normalized = "/".join(normalize_heading(part) for part in item.heading_path.split("/"))
            matches = [heading for heading in outline.headings if heading.normalized_path == normalized]
            if len(matches) != 1:
                raise ContractError("heading path must match exactly once")
            heading = matches[0]
            selections.append(SectionSelection(item.source_id, item.binding, heading.block_id, heading.raw_path, heading.normalized_path, heading.level, scope, True))
        if len({(x.source_id, x.binding, x.normalized_path) for x in selections}) != len(selections):
            raise ContractError("section selections duplicate")
        for index, item in enumerate(selections):
            has_selected_ancestor = any(
                other.source_id == item.source_id
                and item.normalized_path.startswith(other.normalized_path + "/")
                for other in selections
            )
            same_path_winner = next(
                position
                for position, other in enumerate(selections)
                if other.source_id == item.source_id
                and other.normalized_path == item.normalized_path
            )
            remote = not has_selected_ancestor and same_path_winner == index
            selections[index] = SectionSelection(item.source_id, item.binding, item.block_id, item.raw_path, item.normalized_path, item.heading_level, item.acquisition_scope, remote)
        return self.selection.confirm_sections(session_id, tuple(selections))
