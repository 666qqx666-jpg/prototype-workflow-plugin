from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from enterprise_prd.adapters.base import DocumentAdapter
from enterprise_prd.canonical import load_yaml, sha256_bytes
from enterprise_prd.contracts import KnowledgeContract, MissingBehavior
from enterprise_prd.errors import AccessDenied, ContractError, IntegrityError, RevisionConflict
from enterprise_prd.canonical import canonical_json
from enterprise_prd.product_knowledge.models import AcquisitionScope
from enterprise_prd.heading_selection import ExtractedSection, extract_markdown_sections
from enterprise_prd.pack import CapabilityPack


@dataclass(frozen=True)
class DocumentRequest:
    role: str
    document_id: str
    required: bool
    source_id: str | None = None
    heading_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class SectionRequest:
    role: str
    heading_path: str


def bind_sections(
    requests: tuple[DocumentRequest, ...],
    selections: tuple[SectionRequest, ...],
) -> tuple[DocumentRequest, ...]:
    roles = [request.role for request in requests]
    if len(roles) != len(set(roles)):
        raise ContractError("source roles must be unique")
    by_role: dict[str, list[str]] = {role: [] for role in roles}
    pairs: set[tuple[str, str]] = set()
    for selection in selections:
        if type(selection) is not SectionRequest or selection.role not in by_role or type(selection.heading_path) is not str or not selection.heading_path.strip():
            raise ContractError("section selection role is invalid")
        pair = (selection.role, selection.heading_path)
        if pair in pairs:
            raise ContractError("section selection must be unique per role")
        pairs.add(pair)
        by_role[selection.role].append(selection.heading_path)
    if set(by_role) != {selection.role for selection in selections} or any(not paths for paths in by_role.values()):
        raise ContractError("section selection is required for every source role")
    return tuple(
        DocumentRequest(request.role, request.document_id, request.required, request.source_id, tuple(by_role[request.role]))
        for request in requests
    )


@dataclass(frozen=True)
class SourceEvidence:
    roles: tuple[str, ...]
    source_id: str
    document_id: str
    revision: str
    original_content_sha256: str
    retrieved_at: str
    realtime: bool
    selections: tuple["SectionEvidence", ...]


@dataclass(frozen=True)
class SectionEvidence:
    role: str
    heading_path: str
    heading_level: int
    selected_content_sha256: str
    selected_chars: int


@dataclass(frozen=True)
class ContextResult:
    status: str
    contract: KnowledgeContract
    markdown: str
    item_count: int
    source_revisions: tuple[tuple[str, str], ...]
    sources: tuple[SourceEvidence, ...]
    missing: tuple[str, ...]


def _contract(pack: CapabilityPack, stage: str) -> KnowledgeContract:
    raw = load_yaml(pack.root / "contracts/prd.yaml")
    entries = raw.get("contracts")
    if type(entries) is not list:
        raise ContractError("contracts must be a list")
    matches = [
        entry for entry in entries
        if type(entry) is dict and entry.get("stage") == stage
    ]
    if len(matches) != 1:
        raise ContractError(f"stage must have exactly one contract: {stage}")
    entry = matches[0]
    expected = {
        "stage", "required", "optional", "source_scopes", "authority",
        "max_items", "max_chars", "freshness_seconds", "missing_behavior",
        "evidence_required", "writeback",
    }
    if set(entry) != expected:
        raise ContractError(f"knowledge contract fields are invalid: {stage}")
    try:
        missing_behavior = MissingBehavior(entry["missing_behavior"])
    except ValueError as exc:
        raise ContractError("knowledge contract missing_behavior is invalid") from exc
    contract = KnowledgeContract(
        stage=entry["stage"],
        required=tuple(entry["required"]),
        optional=tuple(entry["optional"]),
        source_scopes=tuple(entry["source_scopes"]),
        authority=tuple(entry["authority"]),
        max_items=entry["max_items"],
        max_chars=entry["max_chars"],
        freshness_seconds=entry["freshness_seconds"],
        missing_behavior=missing_behavior,
        evidence_required=entry["evidence_required"],
        writeback=entry["writeback"],
    )
    contract.validate()
    return contract


def assemble_context(
    pack: CapabilityPack,
    stage: str,
    requests: tuple[DocumentRequest, ...],
    adapter: DocumentAdapter,
) -> ContextResult:
    contract = _contract(pack, stage)
    if type(requests) is not tuple:
        raise ContractError("document requests must be a tuple")
    allowed_roles = set(contract.required) | set(contract.optional)
    roles = [request.role for request in requests]
    if len(roles) != len(set(roles)):
        raise ContractError("document request roles must be unique")
    if any(
        type(request) is not DocumentRequest
        or type(request.role) is not str
        or request.role not in allowed_roles
        or type(request.document_id) is not str
        or not request.document_id.strip()
        or type(request.required) is not bool
            or (
                request.source_id is not None
            and (
                type(request.source_id) is not str
                or not request.source_id.strip()
                )
            )
            or type(request.heading_paths) is not tuple
            or any(type(path) is not str or not path.strip() for path in request.heading_paths)
        for request in requests
    ):
        raise ContractError("document request is invalid")

    sections: list[str] = []
    revisions: list[tuple[str, str]] = []
    sources: list[SourceEvidence] = []
    missing: list[str] = []
    required_missing: list[str] = []
    provided_roles: set[str] = set()
    item_count = 0
    grouped: dict[str, list[DocumentRequest]] = {}
    for request in requests:
        grouped.setdefault(request.document_id, []).append(request)
    for document_id, group in grouped.items():
        group_roles = tuple(request.role for request in group)
        source_ids = {
            request.source_id
            for request in group
            if request.source_id is not None
        }
        if len(source_ids) > 1:
            raise ContractError("one document cannot map to multiple source IDs")
        try:
            locked_revision = adapter.revision(document_id)
            document = adapter.read(
                document_id,
                revision=locked_revision,
            )
        except AccessDenied:
            for request in group:
                missing.append(request.role)
                if request.required or request.role in contract.required:
                    required_missing.append(request.role)
            continue
        if (
            document.document_id != document_id
            or document.revision != locked_revision
        ):
            raise RevisionConflict(
                "source changed or returned a different identity"
            )
        content_hash = sha256_bytes(document.content.encode("utf-8"))
        if document.content_hash != content_hash:
            raise ContractError("source content hash is inconsistent")
        role_sections: list[tuple[str, ExtractedSection]] = []
        has_selections = any(request.heading_paths for request in group)
        if has_selections and any(not request.heading_paths for request in group):
            raise ContractError("section selection is required for every role")
        if has_selections:
            for request in group:
                for selected in extract_markdown_sections(document.content, request.heading_paths):
                    role_sections.append((request.role, selected))
            unique_sections: dict[tuple[int, int], ExtractedSection] = {}
            for _, selected in role_sections:
                unique_sections.setdefault((selected.start, selected.end), selected)
            rendered_content = "\n\n".join(
                selected.content.strip()
                for selected in sorted(unique_sections.values(), key=lambda value: value.start)
            )
        else:
            rendered_content = document.content.strip()
        sections.append(
            f"## Source: {', '.join(group_roles)}\n"
            f"- id: {document.document_id}\n"
            f"- revision: {document.revision}\n"
            f"- original_content_sha256: {content_hash}\n\n"
            f"{rendered_content}"
        )
        revisions.append((document.document_id, document.revision))
        provided_roles.update(group_roles)
        sources.append(
            SourceEvidence(
                roles=group_roles,
                source_id=(
                    next(iter(source_ids))
                    if source_ids
                    else document.document_id
                ),
                document_id=document.document_id,
                revision=document.revision,
                original_content_sha256=content_hash,
                retrieved_at=datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
                realtime=document.realtime,
                selections=tuple(
                    SectionEvidence(
                        role=role,
                        heading_path=selected.heading_path,
                        heading_level=selected.heading_level,
                        selected_content_sha256=selected.content_sha256,
                        selected_chars=selected.selected_chars,
                    )
                    for role, selected in role_sections
                ),
            )
        )
        item_count += 1
    standards = sorted((pack.root / "standards").glob("*.md"))
    for standard in standards:
        sections.append(
            f"## Company standard: {standard.name}\n\n"
            f"{standard.read_text(encoding='utf-8').strip()}"
        )
        item_count += 1
    if standards:
        provided_roles.add("company-standards")
        provided_roles.add("company-review-rubric")
    for required_role in contract.required:
        if required_role not in provided_roles:
            missing.append(required_role)
            required_missing.append(required_role)
    if required_missing and contract.missing_behavior is MissingBehavior.BLOCK:
        raise ContractError(
            f"required context is missing: {sorted(set(required_missing))}"
        )
    if item_count > contract.max_items:
        raise ContractError("context item budget exceeded")
    authority = " > ".join(contract.authority)
    markdown = (
        "# Enterprise PRD Context Pack\n\n"
        f"- stage: {contract.stage}\n"
        f"- authority_order: {authority}\n\n"
        + "\n\n".join(sections)
    )
    if len(markdown) > contract.max_chars:
        raise ContractError("context character budget exceeded")
    status = "degraded" if missing else "ready"
    return ContextResult(
        status=status,
        contract=contract,
        markdown=markdown,
        item_count=item_count,
        source_revisions=tuple(revisions),
        sources=tuple(sources),
        missing=tuple(sorted(set(missing))),
    )


@dataclass(frozen=True)
class GrantedContextResult:
    status: str
    contract: KnowledgeContract
    markdown: str
    item_count: int
    source_revisions: tuple[tuple[str, str], ...]
    sources: tuple[dict, ...]
    missing: tuple[str, ...]
    projection_paths: tuple[str, ...]


def assemble_granted_context(pack: CapabilityPack, stage: str, grant: dict, registry, adapters: dict, store) -> GrantedContextResult:
    """Consumes only the sections named by a verified, reserved read grant."""
    if stage != "writer":
        raise ContractError("granted context is writer-only")
    contract = _contract(pack, stage)
    if grant.get("registry_sha256") != registry.sha256:
        raise IntegrityError("grant registry differs from context registry")
    required = set(grant["required_bindings"])
    present = {item["binding"] for item in grant["sources"]}
    if not required <= present:
        raise ContractError("required grant bindings are missing")
    grouped: dict[str, list[dict]] = {}
    for item in grant["sources"]:
        grouped.setdefault(item["source_id"], []).append(item)
    chunks: list[str] = []
    sources: list[dict] = []
    revisions: list[tuple[str, str]] = []
    projection_payloads: list[dict] = []
    for source_id, items in grouped.items():
        target = registry.resolve(source_id)
        adapter_name = target.locator.adapter
        if any(item["adapter"] != adapter_name for item in items):
            raise IntegrityError("grant adapter differs from registry")
        locks = {
            (
                item["revision"],
                item["outline_sha256"],
                item["acquisition_scope"],
            )
            for item in items
        }
        if len(locks) != 1:
            raise ContractError("same source has conflicting grant locks")
        revision, outline_sha256, acquisition_scope = next(iter(locks))
        adapter = adapters[adapter_name]
        observed_revision = adapter.revision(target.token)
        if observed_revision != revision:
            store.invalidate_source_revision(
                source_id,
                locked_revision=revision,
                observed_revision=observed_revision,
            )
            raise RevisionConflict("source revision changed after grant")

        item_evidence: list[tuple[dict, list[dict]]] = []
        rendered: list[str] = []
        if acquisition_scope == AcquisitionScope.NATIVE_SECTIONS.value:
            remote_selections = [
                section
                for item in items
                for section in item["sections"]
                if section["remote_read"]
            ]
            block_ids = tuple(
                dict.fromkeys(section["block_id"] for section in remote_selections)
            )
            if not block_ids:
                raise ContractError("native section grant has no remote reads")
            returned = adapter.read_sections(
                target.token,
                revision=revision,
                block_ids=block_ids,
            )
            if len({section.block_id for section in returned}) != len(returned):
                raise ContractError("adapter returned duplicate sections")
            by_block = {section.block_id: section for section in returned}
            if set(by_block) != set(block_ids):
                raise ContractError("adapter omitted or expanded approved sections")
            for section in returned:
                if section.document_id != target.token or section.revision != revision:
                    store.invalidate_source_revision(
                        source_id,
                        locked_revision=revision,
                        observed_revision=section.revision,
                    )
                    raise RevisionConflict(
                        "section returned a different identity or revision"
                    )
                if (
                    type(section.content) is not str
                    or sha256_bytes(section.content.encode("utf-8"))
                    != section.content_sha256
                ):
                    raise ContractError("section content hash is inconsistent")
            rendered = [by_block[block_id].content for block_id in block_ids]
            for item in items:
                evidence = []
                for selected in item["sections"]:
                    section = by_block.get(selected["block_id"])
                    if section is None and not selected["remote_read"]:
                        covers = [
                            candidate
                            for candidate in remote_selections
                            if candidate["normalized_path"]
                            == selected["normalized_path"]
                            or selected["normalized_path"].startswith(
                                candidate["normalized_path"] + "/"
                            )
                        ]
                        if covers:
                            cover = max(
                                covers,
                                key=lambda candidate: len(
                                    candidate["normalized_path"]
                                ),
                            )
                            section = by_block.get(cover["block_id"])
                    if section is None:
                        raise ContractError("approved section is absent")
                    evidence.append(
                        {
                            "binding": item["binding"],
                            "block_id": selected["block_id"],
                            "raw_path": selected["raw_path"],
                            "normalized_path": selected["normalized_path"],
                            "heading_level": selected["heading_level"],
                            "selected_content_sha256": section.content_sha256,
                            "selected_chars": len(section.content),
                        }
                    )
                item_evidence.append((item, evidence))
            remote_read_scope = "approved_sections"
        elif acquisition_scope == AcquisitionScope.FULL_DOCUMENT_LOCAL_EXTRACT.value:
            document = adapter.read(target.token, revision=revision)
            if document.document_id != target.token or document.revision != revision:
                store.invalidate_source_revision(
                    source_id,
                    locked_revision=revision,
                    observed_revision=document.revision,
                )
                raise RevisionConflict(
                    "full document returned a different identity or revision"
                )
            if sha256_bytes(document.content.encode("utf-8")) != document.content_hash:
                raise ContractError("full document content hash is inconsistent")
            requested_paths = tuple(
                dict.fromkeys(
                    section["raw_path"]
                    for item in items
                    for section in item["sections"]
                )
            )
            rendered = [
                section.content
                for section in extract_markdown_sections(
                    document.content,
                    requested_paths,
                )
            ]
            for item in items:
                evidence = []
                for selected in item["sections"]:
                    extracted = extract_markdown_sections(
                        document.content,
                        (selected["raw_path"],),
                    )[0]
                    evidence.append(
                        {
                            "binding": item["binding"],
                            "block_id": selected["block_id"],
                            "raw_path": selected["raw_path"],
                            "normalized_path": selected["normalized_path"],
                            "heading_level": selected["heading_level"],
                            "selected_content_sha256": sha256_bytes(
                                extracted.content.encode("utf-8")
                            ),
                            "selected_chars": len(extracted.content),
                        }
                    )
                item_evidence.append((item, evidence))
            remote_read_scope = "full_document"
        else:
            raise ContractError("grant acquisition scope is invalid")

        combined_evidence = sorted(
            [entry for _, evidence in item_evidence for entry in evidence],
            key=lambda entry: (
                entry["binding"],
                entry["normalized_path"],
                entry["block_id"],
            ),
        )
        projection_payloads.append(
            {
                "schema_version": 1,
                "projection_type": "selected-section-manifest",
                "extractor_version": "selected-section-v1",
                "registry_sha256": grant["registry_sha256"],
                "source_id": source_id,
                "document_id_sha256": sha256_bytes(target.token.encode("utf-8")),
                "revision": revision,
                "generated_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
                "outline_sha256": outline_sha256,
                "selections": combined_evidence,
            }
        )
        bindings = ", ".join(item["binding"] for item in items)
        chunks.append(
            f"## Source: {bindings}\n"
            f"- source_id: {source_id}\n"
            f"- revision: {revision}\n"
            f"- acquisition_scope: {acquisition_scope}\n\n"
            + "\n\n".join(rendered)
        )
        revisions.append((source_id, revision))
        for item, evidence in item_evidence:
            sources.append(
                {
                    "binding": item["binding"],
                    "source_id": source_id,
                    "revision": revision,
                    "acquisition_scope": acquisition_scope,
                    "remote_read_scope": remote_read_scope,
                    "model_context_scope": "approved_sections",
                    "source_body_persisted": False,
                    "selections": [
                        {
                            "heading_path": entry["normalized_path"],
                            "heading_level": entry["heading_level"],
                            "selected_content_sha256": entry[
                                "selected_content_sha256"
                            ],
                            "selected_chars": entry["selected_chars"],
                        }
                        for entry in evidence
                    ],
                }
            )
    for standard in sorted((pack.root / "standards").glob("*.md")):
        chunks.append(f"## Company standard: {standard.name}\n\n{standard.read_text(encoding='utf-8').strip()}")
    markdown = "# Enterprise PRD Context Pack\n\n- stage: writer\n\n" + "\n\n".join(chunks)
    if len(chunks) > contract.max_items or len(markdown) > contract.max_chars:
        raise ContractError("granted context budget exceeded")
    projections = tuple(
        str(store.write_projection(payload)) for payload in projection_payloads
    )
    missing = tuple(sorted(set(grant.get("missing_optional_bindings", ()))))
    return GrantedContextResult(
        "degraded" if missing else "ready",
        contract,
        markdown,
        len(chunks),
        tuple(revisions),
        tuple(sources),
        missing,
        projections,
    )
