from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .adapters.feishu import FeishuAdapter, FeishuSourceConfig
from .adapters.feishu_readonly import FeishuReadOnlyBridge
from .adapters.local import LocalAdapter
from .canonical import sha256_bytes, sha256_file
from .contracts import compatible_version, runtime_id
from .errors import ContractError, IntegrityError
from .evidence import build_evidence, write_evidence
from .install import (
    PRD_SKILLS,
    install_shared_links,
    rollback_shared_links,
    verify_shared_links,
)
from .manifest import manifest_from_file
from .pack import load_pack
from .prototype.cli import register as register_prototype
from .prototype.cli import run as run_prototype
from .pilot import live_preflight, write_local_registry
from .source_catalog import SourceTarget, SourceLocator, SourceClassification, SourceGovernance, load_source_catalog
from .product_knowledge.models import Lifecycle
from .registry import FileRegistry, build_skill_bundle, build_zip
from .task_start import (
    require_snapshot_catalog, require_snapshot_registry,
    require_snapshot_pack,
    start_new_task,
    write_start_config,
)
from .updater import apply_manifest, inspect_update
from .workflow.context import DocumentRequest, SectionRequest, assemble_context, assemble_granted_context, bind_sections
from .product_knowledge.models import (
    AcquisitionScope, ContextBinding, DocumentCandidate, GrantReceiptStatus, KnowledgeNeed,
    RequestedSection,
)
from .product_knowledge.selection import SelectionService
from .product_knowledge.stores import ProductKnowledgeStore
from .product_knowledge.outline import OutlineWorkflow
from .product_knowledge.grant import ReadGrantService
from .workflow.handoff import create_handoff, load_for_reviewer
from .workflow.review import evaluate_gate, report_from_dict

def document_request(value: str) -> DocumentRequest:
    role, separator, document_id = value.partition("=")
    if separator != "=" or not role.strip() or not document_id.strip():
        raise argparse.ArgumentTypeError("--source must be ROLE=DOCUMENT_ID")
    return DocumentRequest(role.strip(), document_id.strip(), True)


def section_request(value: str) -> SectionRequest:
    role, separator, heading_path = value.partition("=")
    if separator != "=" or not role.strip() or not heading_path.strip():
        raise argparse.ArgumentTypeError("--section must be ROLE=HEADING_PATH")
    return SectionRequest(role.strip(), heading_path.strip())


def runtime_id_argument(value: str) -> str:
    try:
        return runtime_id(value, "runtime ID")
    except ContractError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.new")
    try:
        temporary.write_text(value, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_output_pair(first: Path, first_text: str, second: Path, second_text: str) -> None:
    if first.resolve() == second.resolve():
        raise ContractError("context and metadata outputs must differ")
    pairs = ((first, first_text), (second, second_text))
    temporaries: list[Path] = []
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for path, value in pairs:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.new")
            temporary.write_text(value, encoding="utf-8")
            temporaries.append(temporary)
        for path, _ in pairs:
            if path.exists():
                backup = path.with_name(f".{path.name}.backup")
                os.replace(path, backup)
                backups[path] = backup
        for (path, _), temporary in zip(pairs, temporaries):
            os.replace(temporary, path)
            published.append(path)
        for backup in backups.values():
            backup.unlink()
    except Exception:
        for path in published:
            if path.exists():
                path.unlink()
        for path, backup in backups.items():
            if backup.exists():
                os.replace(backup, path)
        raise
    finally:
        for temporary in temporaries:
            if temporary.exists():
                temporary.unlink()
        for backup in backups.values():
            if backup.exists():
                backup.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(prog="enterprise-prd")
    commands = parser.add_subparsers(dest="command", required=True)
    register_prototype(commands)

    pack = commands.add_parser("pack")
    pack_sub = pack.add_subparsers(dest="pack_command", required=True)
    validate = pack_sub.add_parser("validate")
    validate.add_argument("--path", type=Path, required=True)

    release = commands.add_parser("release")
    release_sub = release.add_subparsers(dest="release_command", required=True)
    build = release_sub.add_parser("build")
    build.add_argument("--source", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build_skill = release_sub.add_parser("build-skill")
    build_skill.add_argument("--package", type=Path, required=True)
    build_skill.add_argument("--skills", type=Path, required=True)
    build_skill.add_argument("--output", type=Path, required=True)
    publish = release_sub.add_parser("publish-pair")
    publish.add_argument("--skill-archive", type=Path, required=True)
    publish.add_argument("--skill-name", required=True)
    publish.add_argument("--skill-version", required=True)
    publish.add_argument("--pack-archive", type=Path, required=True)
    publish.add_argument("--pack-name", required=True)
    publish.add_argument("--pack-version", required=True)
    publish.add_argument("--channel", choices=("canary", "stable"), required=True)
    publish.add_argument("--output", type=Path, required=True)
    publish.add_argument("--published-by", required=True)
    publish.add_argument("--force-update", action="store_true")

    update = commands.add_parser("update")
    update_sub = update.add_subparsers(dest="update_command", required=True)
    apply = update_sub.add_parser("apply")
    apply.add_argument("--manifest", type=Path, required=True)
    apply.add_argument("--registry", type=Path, required=True)
    apply.add_argument("--install-root", type=Path, required=True)
    status = update_sub.add_parser("status")
    status.add_argument("--manifest", type=Path, required=True)
    status.add_argument("--registry", type=Path, required=True)
    status.add_argument("--install-root", type=Path, required=True)

    install = commands.add_parser("install")
    install_sub = install.add_subparsers(
        dest="install_command",
        required=True,
    )
    install_apply = install_sub.add_parser("apply")
    install_apply.add_argument("--source", type=Path, required=True)
    install_apply.add_argument("--home", type=Path)
    install_apply.add_argument("--skill-name", action="append")
    install_apply.add_argument("--receipt", type=Path)
    install_apply.add_argument("--restore-prototype-dependencies", action="store_true")
    install_verify = install_sub.add_parser("verify-shared-links")
    install_verify.add_argument("--source", type=Path, required=True)
    install_verify.add_argument("--home", type=Path)
    install_verify.add_argument("--skill-name", action="append")
    install_rollback = install_sub.add_parser("rollback")
    install_rollback.add_argument("--receipt", type=Path, required=True)
    install_rollback.add_argument("--home", type=Path)
    install_configure = install_sub.add_parser("configure")
    install_configure.add_argument("--registry-url", required=True)
    install_configure.add_argument(
        "--install-root",
        type=Path,
        required=True,
    )
    install_configure.add_argument("--credential-env")
    install_configure.add_argument(
        "--channel",
        choices=("canary", "stable"),
        default="stable",
    )
    install_configure.add_argument(
        "--output",
        type=Path,
        default=Path.home() / ".enterprise-prd/config.yaml",
    )

    task = commands.add_parser("task")
    task_sub = task.add_subparsers(dest="task_command", required=True)
    task_start = task_sub.add_parser("start")
    task_start.add_argument("--run-id", type=runtime_id_argument, required=True)
    task_start.add_argument(
        "--config",
        type=Path,
        default=Path.home() / ".enterprise-prd/config.yaml",
    )
    task_start.add_argument("--source-catalog", type=Path)

    workflow = commands.add_parser("workflow")
    workflow_sub = workflow.add_subparsers(dest="workflow_command", required=True)

    context = workflow_sub.add_parser("context")
    context.add_argument("--pack", type=Path, required=True)
    context.add_argument(
        "--adapter",
        choices=("local", "feishu"),
        default="local",
    )
    context.add_argument("--documents", type=Path)
    context.add_argument("--source-catalog", type=Path)
    context.add_argument("--source", type=document_request, action="append")
    context.add_argument("--section", type=section_request, action="append")
    context.add_argument("--read-grant", type=Path)
    context.add_argument("--selection-root", type=Path)
    context.add_argument("--knowledge-root", type=Path)
    context.add_argument("--stage", choices=("discovery", "writer", "reviewer"), required=True)
    context.add_argument("--output", type=Path, required=True)
    context.add_argument("--metadata-output", type=Path, required=True)
    context.add_argument("--run-snapshot", type=Path, required=True)

    selection = commands.add_parser("selection")
    selection_sub = selection.add_subparsers(dest="selection_command", required=True)
    for name in ("create", "candidates", "confirm-documents", "outline", "confirm-sections", "grant"):
        value = selection_sub.add_parser(name)
        value.add_argument("--session-id", type=runtime_id_argument, required=True)
        value.add_argument("--source-catalog", type=Path, required=True)
        value.add_argument("--pack", type=Path, required=True)
        value.add_argument("--selection-root", type=Path, required=True)
        value.add_argument("--knowledge-root", type=Path)
        if name == "create":
            value.add_argument("--run-id", type=runtime_id_argument, required=True); value.add_argument("--run-snapshot", type=Path, required=True); value.add_argument("--requirement-file", type=Path, required=True); value.add_argument("--required-binding", action="append", required=True); value.add_argument("--optional-binding", action="append", default=[])
        elif name in {"candidates", "confirm-sections"}: value.add_argument("--input", type=Path, required=True)
        elif name == "confirm-documents": value.add_argument("--source-id", action="append", required=True)
        elif name == "grant": value.add_argument("--grant-id", type=runtime_id_argument, required=True); value.add_argument("--run-snapshot", type=Path, required=True)

    handoff = workflow_sub.add_parser("handoff")
    handoff.add_argument("--run-id", type=runtime_id_argument, required=True)
    handoff.add_argument("--writer-context-id", type=runtime_id_argument, required=True)
    handoff.add_argument("--prd", type=Path, required=True)
    handoff.add_argument("--context-pack", type=Path, required=True)
    handoff.add_argument("--run-snapshot", type=Path, required=True)
    handoff.add_argument("--decision-ledger", type=Path, required=True)
    handoff.add_argument("--source-revisions", type=Path, required=True)
    handoff.add_argument("--output", type=Path, required=True)

    reviewer = workflow_sub.add_parser("reviewer-init")
    reviewer.add_argument("--handoff", type=Path, required=True)
    reviewer.add_argument("--review-context-id", type=runtime_id_argument, required=True)

    gate = workflow_sub.add_parser("gate")
    gate.add_argument("--report", type=Path, required=True)
    gate.add_argument("--handoff", type=Path, required=True)

    pilot = commands.add_parser("pilot")
    pilot_sub = pilot.add_subparsers(dest="pilot_command", required=True)
    configure_source = pilot_sub.add_parser("configure-source")
    configure_source.add_argument("--output", type=Path, required=True)
    pilot_verify = pilot_sub.add_parser("verify")
    pilot_verify.add_argument("--registry", type=Path, required=True)
    pilot_verify.add_argument("--pack", type=Path, required=True)
    pilot_verify.add_argument("--manifest", type=Path, required=True)
    live = pilot_sub.add_parser("live-preflight")
    live.add_argument("--pack", type=Path, required=True)
    live.add_argument("--read-only", action="store_true")

    evidence = commands.add_parser("evidence")
    evidence.add_argument("--repo", type=Path, default=Path.cwd())
    evidence.add_argument("--require-tag", action="append", required=True)
    evidence.add_argument("--manifest", type=Path, required=True)
    evidence.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "selection":
        if args.selection_command in {"create", "grant"}:
            snapshot = require_snapshot_pack(args.run_snapshot, args.pack)
            catalog = require_snapshot_registry(
                args.run_snapshot,
                args.source_catalog,
                args.pack,
            )
        else:
            snapshot = None
            catalog = load_source_catalog(args.source_catalog, args.pack)
        service = SelectionService(args.selection_root, catalog)
        if args.selection_command == "create":
            if snapshot["run_id"] != args.run_id:
                raise IntegrityError("selection run ID differs from snapshot")
            need = KnowledgeNeed(args.requirement_file.read_text(encoding="utf-8").strip(), tuple(ContextBinding(x) for x in args.required_binding), tuple(ContextBinding(x) for x in args.optional_binding)); state = service.create(session_id=args.session_id, run_id=args.run_id, run_snapshot_sha256=sha256_file(args.run_snapshot), registry_sha256=catalog.sha256, need=need)
        elif args.selection_command == "candidates":
            raw = json.loads(args.input.read_text(encoding="utf-8"))
            if set(raw) != {"schema_version", "candidates"} or raw["schema_version"] != 1: raise ContractError("candidate input is invalid")
            state = service.set_candidates(args.session_id, tuple(DocumentCandidate(item["source_id"], ContextBinding(item["binding"]), item["reason"]) for item in raw["candidates"]))
        elif args.selection_command == "confirm-documents": state = service.confirm_documents(args.session_id, tuple(args.source_id))
        elif args.selection_command == "outline":
            if args.knowledge_root is None: raise ContractError("outline requires knowledge root")
            current=service.load(args.session_id); tokens=tuple(catalog.resolve(item.source_id).token for item in current.confirmed_documents); state = OutlineWorkflow(service, ProductKnowledgeStore(args.knowledge_root), catalog, {"feishu": FeishuReadOnlyBridge(tokens)}).preflight(args.session_id); print(json.dumps({"status":"outlines_ready","sources":[item.source_id for item in state]})); return 0
        elif args.selection_command == "confirm-sections":
            if args.knowledge_root is None: raise ContractError("sections require knowledge root")
            raw=json.loads(args.input.read_text(encoding="utf-8"))
            if type(raw) is not dict or set(raw)!={"schema_version","acquisition_scope","sections"} or raw["schema_version"]!=1 or type(raw["sections"]) is not list: raise ContractError("section input is invalid")
            workflow0=OutlineWorkflow(service,ProductKnowledgeStore(args.knowledge_root),catalog,{})
            state=workflow0.confirm_sections(args.session_id,tuple(RequestedSection(item["source_id"],ContextBinding(item["binding"]),item["heading_path"]) for item in raw["sections"]),AcquisitionScope(raw["acquisition_scope"]))
        else:
            if args.knowledge_root is None: raise ContractError("grant requires knowledge root")
            current=service.load(args.session_id)
            if snapshot["run_id"]!=current.run_id or sha256_file(args.run_snapshot)!=current.run_snapshot_sha256: raise IntegrityError("grant run snapshot differs from selection")
            tokens=tuple(catalog.resolve(item.source_id).token for item in current.section_selections); path=ReadGrantService(args.selection_root,service,ProductKnowledgeStore(args.knowledge_root),catalog,{"feishu":FeishuReadOnlyBridge(tuple(dict.fromkeys(tokens)))}).issue(args.session_id,grant_id=args.grant_id); print(json.dumps({"status":"grant_ready","grant":str(path)})); return 0
        print(json.dumps({"status":state.status.value,"session_id":state.session_id}))
        return 0
    if args.command == "pack" and args.pack_command == "validate":
        loaded = load_pack(args.path)
        print(json.dumps({"status": "pass", "pack_id": loaded.manifest.pack_id, "version": loaded.manifest.version}))
        return 0
    if args.command == "release" and args.release_command == "build":
        digest = build_zip(args.source, args.output)
        print(json.dumps({"status": "pass", "archive": str(args.output), "sha256": digest}))
        return 0
    if args.command == "release" and args.release_command == "build-skill":
        digest = build_skill_bundle(args.package, args.skills, args.output)
        print(json.dumps({"status": "pass", "archive": str(args.output), "sha256": digest}))
        return 0
    if args.command == "release" and args.release_command == "publish-pair":
        output_parent = args.output.resolve().parent
        payload = {
            "schema_version": 1,
            "channel": args.channel,
            "skill": {
                "name": args.skill_name,
                "version": args.skill_version,
                "archive": os.path.relpath(args.skill_archive.resolve(), output_parent),
                "sha256": sha256_file(args.skill_archive),
            },
            "company_pack": {
                "name": args.pack_name,
                "version": args.pack_version,
                "archive": os.path.relpath(args.pack_archive.resolve(), output_parent),
                "sha256": sha256_file(args.pack_archive),
            },
            "compatible_skill": f">={args.skill_version},<{int(args.skill_version.split('.')[0]) + 1}.0.0",
            "published_by": args.published_by,
            "published_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "force_update": args.force_update,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        manifest_from_file(args.output)
        print(json.dumps({"status": "pass", "manifest": str(args.output)}))
        return 0
    if args.command == "update" and args.update_command == "apply":
        installed = apply_manifest(manifest_from_file(args.manifest), FileRegistry(args.registry), args.install_root)
        print(json.dumps({"status": "pass", "installed": str(installed)}))
        return 0
    if args.command == "update" and args.update_command == "status":
        update_status = inspect_update(
            manifest_from_file(args.manifest),
            FileRegistry(args.registry),
            args.install_root,
        )
        print(json.dumps({"status": update_status.value}))
        return (
            0
            if update_status.value
            in {"current", "update_available", "degraded_cache"}
            else 4
        )
    if args.command == "prototype":
        return run_prototype(args)
    if args.command == "install" and args.install_command == "apply":
        names = tuple(args.skill_name) if args.skill_name else PRD_SKILLS
        install_shared_links(
            args.source,
            home=args.home,
            skill_names=names,
            receipt_path=args.receipt,
            restore_prototype_dependencies=args.restore_prototype_dependencies,
        )
        print(json.dumps({"status": "pass"}))
        return 0
    if (
        args.command == "install"
        and args.install_command == "verify-shared-links"
    ):
        names = tuple(args.skill_name) if args.skill_name else PRD_SKILLS
        result = verify_shared_links(args.source, home=args.home, skill_names=names)
        passed = all(result.values())
        print(json.dumps({
            "status": "pass" if passed else "blocked",
            "skills": result,
        }))
        return 0 if passed else 5
    if args.command == "install" and args.install_command == "rollback":
        result = rollback_shared_links(args.receipt, home=args.home)
        print(json.dumps(result))
        return 0
    if args.command == "install" and args.install_command == "configure":
        write_start_config(
            args.registry_url,
            args.install_root,
            args.output,
            args.credential_env,
            args.channel,
        )
        print(json.dumps({
            "status": "pass",
            "config": str(args.output),
        }))
        return 0
    if args.command == "task" and args.task_command == "start":
        result = start_new_task(
            args.run_id,
            args.config,
            args.source_catalog,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 6 if result["status"] == "restart_required" else 0
    if args.command == "workflow" and args.workflow_command == "context":
        require_snapshot_pack(args.run_snapshot, args.pack)
        pack = load_pack(args.pack)
        requests = tuple(args.source or ())
        selections = tuple(args.section or ())
        catalog = None
        if args.adapter == "local":
            if args.documents is None or args.source_catalog is not None or selections or args.read_grant is not None:
                raise ContractError(
                    "local context requires --documents and forbids "
                    "--source-catalog and --read-grant"
                )
            if not requests:
                raise ContractError("local context requires --source")
            adapter = LocalAdapter(args.documents)
        else:
            if args.documents is not None or args.source_catalog is None or args.read_grant is None:
                raise ContractError(
                    "Feishu context requires --read-grant and --source-catalog"
                )
            if requests or selections:
                raise ContractError("Feishu context forbids --source and --section; use --read-grant")
            if args.selection_root is None or args.knowledge_root is None:
                raise ContractError("Feishu context requires selection and knowledge roots")
            catalog = require_snapshot_registry(
                args.run_snapshot,
                args.source_catalog,
                args.pack,
            )
            selection_service = SelectionService(args.selection_root, catalog)
            knowledge_store = ProductKnowledgeStore(args.knowledge_root)
            grants = ReadGrantService(
                args.selection_root,
                selection_service,
                knowledge_store,
                catalog,
                {},
            )
            snapshot_sha256 = sha256_file(args.run_snapshot)
            grant = grants.load(
                args.read_grant,
                expected_run_snapshot_sha256=snapshot_sha256,
                require_active=True,
            )
            receipt = grants.reserve(
                args.read_grant,
                expected_run_snapshot_sha256=snapshot_sha256,
            )
            try:
                document_ids = tuple(
                    dict.fromkeys(
                        catalog.resolve(item["source_id"]).token
                        for item in grant["sources"]
                    )
                )
                adapter = FeishuReadOnlyBridge(document_ids)
                result = assemble_granted_context(pack, args.stage, grant, catalog, {"feishu": adapter}, knowledge_store)
                metadata = {"schema_version": 4, "adapter": "feishu-readonly", "session_id": grant["session_id"], "grant_id": grant["grant_id"], "grant_sha256": grant["grant_sha256"], "registry_sha256": grant["registry_sha256"], "source_revisions": [list(item) for item in result.source_revisions], "sources": list(result.sources)}
                _atomic_output_pair(args.output, result.markdown, args.metadata_output, json.dumps(metadata, ensure_ascii=False, indent=2))
                grants.selection.mark_consumed(grant["session_id"], grant["grant_id"])
                grants.finish(receipt, status=GrantReceiptStatus.CONSUMED)
            except Exception:
                grants.finish(receipt, status=GrantReceiptStatus.FAILED, reason="context_output_failed")
                try: grants.selection.abandon_grant(grant["session_id"], grant["grant_id"], "context_output_failed")
                except ContractError: pass
                raise
            print(json.dumps({"status": result.status, "output": str(args.output), "sources": result.source_revisions}))
            return 0
        result = assemble_context(pack, args.stage, requests, adapter)
        metadata = {
            "source_revisions": [
                list(item) for item in result.source_revisions
            ],
        }
        if catalog is not None:
            metadata.update({
                "schema_version": 3,
                "adapter": "feishu-readonly",
                "catalog_sha256": catalog.sha256,
                "sources": [
                    {
                        "roles": list(source.roles),
                        "source_id": source.source_id,
                        "document_id": source.document_id,
                        "revision": source.revision,
                        "original_content_sha256": source.original_content_sha256,
                        "retrieved_at": source.retrieved_at,
                        "realtime": source.realtime,
                        "selections": [
                            {
                                "role": selection.role,
                                "heading_path": selection.heading_path,
                                "heading_level": selection.heading_level,
                                "selected_content_sha256": selection.selected_content_sha256,
                                "selected_chars": selection.selected_chars,
                            }
                            for selection in source.selections
                        ],
                    }
                    for source in result.sources
                ],
            })
        metadata_text = json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
        )
        _atomic_output_pair(args.output, result.markdown, args.metadata_output, metadata_text)
        print(json.dumps({
            "status": result.status,
            "output": str(args.output),
            "sources": result.source_revisions,
        }))
        return 0
    if args.command == "workflow" and args.workflow_command == "handoff":
        ledger = json.loads(args.decision_ledger.read_text(encoding="utf-8"))
        revisions = json.loads(args.source_revisions.read_text(encoding="utf-8"))
        handoff_result = create_handoff(
            run_id=args.run_id,
            writer_context_id=args.writer_context_id,
            prd_path=args.prd,
            context_pack_sha256=sha256_file(args.context_pack),
            run_snapshot_path=args.run_snapshot,
            confirmed_decision_ids=tuple(ledger["confirmed_decision_ids"]),
            unresolved_decision_ids=tuple(ledger["unresolved_decision_ids"]),
            source_revisions=tuple(
                tuple(item) for item in revisions["source_revisions"]
            ),
            output=args.output,
        )
        print(json.dumps({
            "status": "pass",
            "handoff": str(args.output),
            "prd_sha256": handoff_result.prd_sha256,
        }))
        return 0
    if args.command == "workflow" and args.workflow_command == "reviewer-init":
        handoff_result = load_for_reviewer(
            args.handoff,
            args.review_context_id,
        )
        print(json.dumps({
            "status": "pass",
            "run_id": handoff_result.run_id,
            "prd_path": handoff_result.prd_path,
        }))
        return 0
    if args.command == "workflow" and args.workflow_command == "gate":
        report = report_from_dict(
            json.loads(args.report.read_text(encoding="utf-8"))
        )
        handoff_result = load_for_reviewer(
            args.handoff,
            report.review_context_id,
        )
        identity_reasons = []
        if report.run_id != handoff_result.run_id:
            identity_reasons.append("run_id_mismatch")
        if report.reviewed_artifact_sha256 != handoff_result.prd_sha256:
            identity_reasons.append("artifact_hash_mismatch")
        decision = evaluate_gate(report)
        reason_codes = tuple(identity_reasons) + decision.reason_codes
        print(json.dumps({
            "status": "pass" if not reason_codes else "blocked",
            "reason_codes": reason_codes,
        }))
        return 0 if not reason_codes else 3
    if args.command == "pilot" and args.pilot_command == "configure-source":
        expected_output = Path(
            "packs/company-product/sources/catalog.local.yaml"
        ).resolve()
        if args.output.resolve() != expected_output:
            raise ContractError(f"source catalog output must be {expected_output}")
        targets = []
        while True:
            source_id = input("source id (empty to finish): ").strip()
            if not source_id:
                break
            adapter = input("adapter: ").strip()
            token = input("canonical document token: ").strip()
            product_domain = input("product domain: ").strip()
            module = input("module: ").strip()
            material_type = input("material type: ").strip()
            lifecycle_text = input("lifecycle: ").strip()
            replacements = input("superseded by (comma-separated): ").strip()
            try:
                lifecycle = Lifecycle(lifecycle_text)
            except ValueError as exc:
                raise ContractError("source lifecycle is invalid") from exc
            targets.append(
                SourceTarget(
                    source_id,
                    "document",
                    SourceLocator(adapter, token),
                    SourceClassification(
                        product_domain,
                        module,
                        material_type,
                    ),
                    SourceGovernance(
                        lifecycle,
                        tuple(
                            item.strip()
                            for item in replacements.split(",")
                            if item.strip()
                        ),
                    ),
                )
            )
        write_local_registry(
            tuple(targets),
            args.output,
            expected_output.parents[1],
        )
        print(json.dumps({
            "status": "pass",
            "targets": len(targets),
            "output": str(args.output),
        }))
        return 0
    if args.command == "pilot" and args.pilot_command == "verify":
        registry_root = args.registry.resolve()
        manifest_path = args.manifest.resolve()
        if registry_root not in manifest_path.parents:
            raise ContractError(
                "release manifest must be inside the selected registry"
            )
        pack = load_pack(args.pack)
        manifest = manifest_from_file(manifest_path)
        if manifest.company_pack.name != pack.manifest.pack_id:
            raise ContractError(
                "stable manifest pack ID does not match loaded pack"
            )
        if manifest.company_pack.version != pack.manifest.version:
            raise ContractError(
                "stable manifest pack version does not match loaded pack"
            )
        if not compatible_version(
            manifest.skill.version,
            pack.manifest.compatible_skill,
        ):
            raise ContractError(
                "loaded pack is incompatible with stable Skill"
            )
        registry = FileRegistry(registry_root)
        for ref in (manifest.skill, manifest.company_pack):
            if sha256_bytes(registry.fetch_bytes(ref.archive)) != ref.sha256:
                raise IntegrityError(f"archive hash mismatch: {ref.name}")
        print(json.dumps({
            "status": "ready_for_scoped_live_preflight",
            "pack_id": pack.manifest.pack_id,
            "skill_version": manifest.skill.version,
            "pack_version": manifest.company_pack.version,
        }))
        return 0
    if args.command == "pilot" and args.pilot_command == "live-preflight":
        if not args.read_only:
            raise ContractError("live-preflight requires --read-only")
        catalog_path = args.pack / "sources/catalog.local.yaml"
        catalog = load_source_catalog(catalog_path, args.pack)
        if any(
            target.kind != "document"
            or target.locator.adapter != "feishu"
            for target in catalog.sources
        ):
            raise ContractError(
                "live preflight accepts Feishu document sources only"
            )
        document_ids = tuple(target.token for target in catalog.sources)
        adapter = FeishuAdapter(
            FeishuSourceConfig(document_ids=document_ids)
        )
        print(json.dumps(
            live_preflight(document_ids, adapter),
            ensure_ascii=False,
        ))
        return 0
    if args.command == "evidence":
        payload = build_evidence(
            args.repo.resolve(),
            tuple(args.require_tag),
            args.manifest,
        )
        write_evidence(payload, args.output)
        print(json.dumps({
            "status": "pass",
            "output": str(args.output),
        }))
        return 0
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())
