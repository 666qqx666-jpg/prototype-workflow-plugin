from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from enterprise_prd.canonical import canonical_json
from enterprise_prd.errors import ContractError, EnterprisePrdError

from .adapters.fixture import FixtureSourceReader
from .assembler import ContextAssembler
from .contracts import AcquisitionScope, RequestedSection
from .directory import YamlSourceDirectory
from .grant import ReadGrantService
from .runtime import load_json
from .workflow import LocalContextWorkflow


_CANDIDATE_FIELDS = {"source_id", "purpose"}
_SELECTION_FIELDS = {"source_id", "raw_path", "acquisition_scope"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="local-context")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(name: str) -> argparse.ArgumentParser:
        command = subparsers.add_parser(name)
        command.add_argument("--directory", required=True, type=Path)
        command.add_argument("--runtime-root", required=True, type=Path)
        return command

    create = common("create")
    create.add_argument("--requirement", required=True)
    create.add_argument("--candidates", required=True, type=Path)

    confirm_documents = common("confirm-documents")
    confirm_documents.add_argument("--session-id", required=True)
    confirm_documents.add_argument("--source-id", action="append", required=True)

    outline = common("outline")
    outline.add_argument("--session-id", required=True)
    outline.add_argument("--fixture-sources", required=True, type=Path)

    confirm_sections = common("confirm-sections")
    confirm_sections.add_argument("--session-id", required=True)
    confirm_sections.add_argument("--selections", required=True, type=Path)

    grant = common("grant")
    grant.add_argument("--session-id", required=True)

    context = common("context")
    context.add_argument("--session-id", required=True)
    context.add_argument("--fixture-sources", required=True, type=Path)

    show = common("show")
    show.add_argument("--session-id", required=True)
    return parser


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError("local context input is unavailable or corrupt") from exc
    if type(value) is not dict:
        raise ContractError("local context input must be an object")
    return value


def _candidates(path: Path) -> tuple[tuple[str, str], ...]:
    raw = _json(path)
    if (
        set(raw) != {"schema_version", "candidates"}
        or raw["schema_version"] != 1
        or type(raw["candidates"]) is not list
        or not raw["candidates"]
        or any(
            type(item) is not dict or set(item) != _CANDIDATE_FIELDS
            for item in raw["candidates"]
        )
    ):
        raise ContractError("candidate input fields are invalid")
    return tuple(
        (item["source_id"], item["purpose"])
        for item in raw["candidates"]
    )


def _selections(path: Path) -> tuple[RequestedSection, ...]:
    raw = _json(path)
    if (
        set(raw) != {"schema_version", "sections"}
        or raw["schema_version"] != 1
        or type(raw["sections"]) is not list
        or not raw["sections"]
        or any(
            type(item) is not dict or set(item) != _SELECTION_FIELDS
            for item in raw["sections"]
        )
    ):
        raise ContractError("selection input fields are invalid")
    try:
        return tuple(
            RequestedSection(
                item["source_id"],
                item["raw_path"],
                AcquisitionScope(item["acquisition_scope"]),
            )
            for item in raw["sections"]
        )
    except ValueError as exc:
        raise ContractError("selection acquisition scope is invalid") from exc


def _print(value: dict, *, stream=None) -> None:
    print(canonical_json(value).decode("utf-8"), file=stream or sys.stdout)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        directory = YamlSourceDirectory.load(args.directory)
        readers = {}
        fixture_sources = getattr(args, "fixture_sources", None)
        if fixture_sources is not None:
            readers["fixture"] = FixtureSourceReader.load(fixture_sources)
        workflow = LocalContextWorkflow(args.runtime_root, directory, readers)

        if args.command == "create":
            state = workflow.create(args.requirement, _candidates(args.candidates))
            summary = {"status": state["status"], "session_id": state["session_id"]}
        elif args.command == "confirm-documents":
            state = workflow.confirm_documents(args.session_id, tuple(args.source_id))
            summary = {
                "status": state["status"],
                "source_ids": state["confirmed_source_ids"],
            }
        elif args.command == "outline":
            state = workflow.collect_outlines(args.session_id)
            summary = {
                "status": state["status"],
                "outline_sources": [item["source_id"] for item in state["outlines"]],
            }
        elif args.command == "confirm-sections":
            state = workflow.confirm_sections(
                args.session_id,
                _selections(args.selections),
            )
            summary = {
                "status": state["status"],
                "selected_count": len(state["selections"]),
            }
        elif args.command == "grant":
            service = ReadGrantService(args.runtime_root, workflow)
            path = service.issue(args.session_id)
            grant = service.load(path)
            summary = {
                "status": workflow.load(args.session_id)["status"],
                "grant_id": grant["grant_id"],
                "grant_sha256": grant["grant_sha256"],
            }
        elif args.command == "context":
            workflow.load(args.session_id)
            session_directory = workflow._directory(args.session_id)
            ContextAssembler(args.runtime_root, workflow, readers).assemble(
                session_directory / "read-grant.json"
            )
            receipt = load_json(session_directory / "consumption.json")
            summary = {
                "status": "consumed",
                "context_sha256": receipt["context_sha256"],
            }
        elif args.command == "show":
            state = workflow.load(args.session_id)
            summary = {"status": state["status"], "session_id": state["session_id"]}
        else:
            raise ContractError("local context command is invalid")
        _print(summary)
        return 0
    except EnterprisePrdError as exc:
        _print(
            {"status": "error", "code": exc.code, "message": str(exc)},
            stream=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
