from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from enterprise_prd.canonical import canonical_json
from enterprise_prd.errors import ContractError, EnterprisePrdError

from ..assembler import ContextAssembler
from ..contracts import SessionStatus
from ..directory import YamlSourceDirectory
from ..runtime import load_json
from ..workflow import LocalContextWorkflow
from .feishu_reader import FeishuSourceReader, ReadOnlyCommandRunner
from .feishu_resolver import (
    HeadingReference,
    PrivateLocatorResolver,
    SessionHeadingIndex,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="local-context-feishu")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("live-outline", "live-context"):
        command = subparsers.add_parser(name)
        command.add_argument("--directory", required=True, type=Path)
        command.add_argument("--secrets", required=True, type=Path)
        command.add_argument("--runtime-root", required=True, type=Path)
        command.add_argument("--session-id", required=True)
    return parser


def _index_from_validated_state(state: dict) -> SessionHeadingIndex:
    references = tuple(
        HeadingReference(
            outline["source_id"],
            outline["revision"],
            heading["normalized_path"],
            heading["block_id"],
        )
        for outline in state["outlines"]
        for heading in outline["headings"]
    )
    return SessionHeadingIndex(references)


def _print(value: dict, *, stream=None) -> None:
    print(canonical_json(value).decode("utf-8"), file=stream or sys.stdout)


class _OutlineFailureGuard:
    def __init__(self, reader, workflow: LocalContextWorkflow, snapshot) -> None:
        self.reader = reader
        self.workflow = workflow
        self.snapshot = snapshot

    def get_outline(self, source):
        try:
            return self.reader.get_outline(source)
        except ContractError:
            self.workflow._terminal_from_snapshot(
                self.snapshot,
                SessionStatus.INVALID_OUTLINE,
            )
            raise

    def read_sections(self, source, revision, approved_paths):
        return self.reader.read_sections(source, revision, approved_paths)


def main(argv: list[str] | None = None, *, runner=None) -> int:
    args = _parser().parse_args(argv)
    try:
        directory = YamlSourceDirectory.load(args.directory)
        resolver = PrivateLocatorResolver.load(args.secrets)
        base_workflow = LocalContextWorkflow(args.runtime_root, directory, {})
        state = base_workflow.load(args.session_id)
        if args.command == "live-outline":
            reader = FeishuSourceReader(
                runner or ReadOnlyCommandRunner(),
                resolver,
                SessionHeadingIndex.empty(),
            )
            workflow = LocalContextWorkflow(args.runtime_root, directory, {})
            snapshot = workflow._snapshot_from_state(
                state,
                SessionStatus.DOCUMENTS_CONFIRMED,
                "collect_feishu_outline",
            )
            workflow.readers["feishu"] = _OutlineFailureGuard(
                reader,
                workflow,
                snapshot,
            )
            state = workflow.collect_outlines(args.session_id)
            if len(state["outlines"]) != 1:
                raise ContractError("live Outline requires exactly one source")
            outline = state["outlines"][0]
            summary = {
                "status": state["status"],
                "source_id": outline["source_id"],
                "document_id_sha256": outline["document_id_sha256"],
                "title": outline["title"],
                "revision": outline["revision"],
                "headings": [
                    {
                        "block_id": item["block_id"],
                        "title": item["title"],
                        "level": item["level"],
                        "raw_path": item["raw_path"],
                        "normalized_path": item["normalized_path"],
                    }
                    for item in outline["headings"]
                ],
            }
        elif args.command == "live-context":
            index = _index_from_validated_state(state)
            reader = FeishuSourceReader(
                runner or ReadOnlyCommandRunner(),
                resolver,
                index,
            )
            workflow = LocalContextWorkflow(
                args.runtime_root,
                directory,
                {"feishu": reader},
            )
            session_directory = workflow._directory(args.session_id)
            ContextAssembler(args.runtime_root, workflow, {"feishu": reader}).assemble(
                session_directory / "read-grant.json"
            )
            context = load_json(session_directory / "context.json")
            receipt = load_json(session_directory / "consumption.json")
            sections = [
                section
                for source in context["sources"]
                for section in source["sections"]
            ]
            summary = {
                "status": "consumed",
                "context_sha256": receipt["context_sha256"],
                "approved_paths": [item["raw_path"] for item in sections],
                "approved_character_count": sum(len(item["content"]) for item in sections),
            }
        else:
            raise ContractError("Feishu smoke command is invalid")
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
