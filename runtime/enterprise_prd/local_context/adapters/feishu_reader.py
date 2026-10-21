from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Callable

from enterprise_prd.canonical import sha256_bytes
from enterprise_prd.errors import AccessDenied, ContractError
from enterprise_prd.heading_selection import normalize_heading

from ..contracts import (
    AcquisitionScope,
    AdapterOutline,
    AdapterSection,
    ApprovedPath,
    DirectoryEntry,
    Heading,
)
from ..errors import SourceUnavailable
from .feishu_resolver import PrivateLocatorResolver, SessionHeadingIndex


_ACCESS_MARKERS = ("permission denied", "forbidden", "missing scope", "not found")
_HEADING_TAGS = {f"h{level}" for level in range(1, 7)}


class ReadOnlyCommandRunner:
    def __init__(self, *, run: Callable = subprocess.run) -> None:
        self.run = run

    def _json(self, arguments: tuple[str, ...]) -> dict:
        argv = ["lark-cli", *arguments, "--as", "user", "--format", "json"]
        try:
            result = self.run(argv, text=True, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SourceUnavailable("Feishu source is unavailable") from exc
        if result.returncode != 0:
            lowered = (result.stderr or result.stdout).casefold()
            if any(marker in lowered for marker in _ACCESS_MARKERS):
                raise AccessDenied("Feishu source access was denied")
            raise SourceUnavailable("Feishu source is unavailable")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ContractError("Feishu returned invalid JSON") from exc
        if type(payload) is not dict:
            raise ContractError("Feishu JSON root must be an object")
        return payload

    def fetch_outline(self, document_id: str) -> dict:
        return self._json(
            (
                "docs", "+fetch", "--doc", document_id,
                "--doc-format", "xml", "--detail", "with-ids",
                "--scope", "outline", "--max-depth", "6",
            )
        )

    def fetch_section(self, document_id: str, block_id: str, revision: str) -> dict:
        return self._json(
            (
                "docs", "+fetch", "--doc", document_id,
                "--doc-format", "markdown", "--detail", "simple",
                "--scope", "section", "--start-block-id", block_id,
                "--revision-id", revision,
            )
        )


class FeishuSourceReader:
    def __init__(
        self,
        runner,
        resolver: PrivateLocatorResolver,
        heading_index: SessionHeadingIndex,
    ) -> None:
        self.runner = runner
        self.resolver = resolver
        self.heading_index = heading_index

    @staticmethod
    def _whitespace_only(value: str | None) -> bool:
        return value is None or not value.strip()

    @staticmethod
    def _document(payload: dict) -> dict:
        data = payload.get("data")
        document = data.get("document") if type(data) is dict else None
        if type(document) is not dict:
            raise ContractError("Feishu response lacks a document")
        return document

    def _source_token(self, source: DirectoryEntry) -> str:
        if source.adapter != "feishu":
            raise ContractError(f"source adapter is invalid: {source.source_id}")
        return self.resolver.resolve(source)

    def get_outline(self, source: DirectoryEntry) -> AdapterOutline:
        token = self._source_token(source)
        payload = self.runner.fetch_outline(token)
        document = self._document(payload)
        revision_value = document.get("revision_id")
        if (
            document.get("document_id") != token
            or type(document.get("title")) is not str
            or not document["title"].strip()
            or not (
                (type(revision_value) is int and revision_value >= 0)
                or (type(revision_value) is str and bool(revision_value.strip()))
            )
            or type(document.get("content")) is not str
        ):
            raise ContractError("Feishu outline identity or fields are invalid")
        content = document["content"]
        if "<!" in content or "<?" in content:
            raise ContractError("Feishu outline XML contains a forbidden construct")
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise ContractError("Feishu outline XML is malformed") from exc
        if (
            root.tag != "fragment"
            or root.attrib != {"mode": "outline"}
            or not self._whitespace_only(root.text)
            or not self._whitespace_only(root.tail)
            or len(root) != 1
        ):
            raise ContractError("Feishu outline root is invalid")
        wrapper = root[0]
        if (
            wrapper.tag != "outline"
            or wrapper.attrib
            or not self._whitespace_only(wrapper.text)
            or not self._whitespace_only(wrapper.tail)
            or not len(wrapper)
        ):
            raise ContractError("Feishu outline wrapper is invalid")
        stack: list[tuple[int, str, str]] = []
        headings = []
        for element in wrapper:
            if (
                element.tag not in _HEADING_TAGS
                or set(element.attrib) != {"id"}
                or not element.attrib["id"].strip()
                or len(element)
                or type(element.text) is not str
                or not element.text.strip()
                or not self._whitespace_only(element.tail)
            ):
                raise ContractError("Feishu outline heading is invalid")
            level = int(element.tag[1])
            title = element.text.strip()
            normalized_title = normalize_heading(title)
            while stack and stack[-1][0] >= level:
                stack.pop()
            raw_path = "/".join([item[1] for item in stack] + [title])
            normalized_path = "/".join([item[2] for item in stack] + [normalized_title])
            heading = Heading(
                element.attrib["id"],
                title,
                normalized_title,
                level,
                raw_path,
                normalized_path,
            )
            heading.validate()
            headings.append(heading)
            stack.append((level, title, normalized_title))
        for field in ("block_id", "raw_path", "normalized_path"):
            values = [getattr(item, field) for item in headings]
            if len(values) != len(set(values)):
                raise ContractError("Feishu outline identities duplicate")
        return AdapterOutline(
            source.source_id,
            sha256_bytes(token.encode("utf-8")),
            document["title"].strip(),
            str(revision_value),
            tuple(headings),
            (AcquisitionScope.NATIVE_SECTIONS,),
        )

    def read_sections(
        self,
        source: DirectoryEntry,
        revision: str,
        approved_paths: tuple[ApprovedPath, ...],
    ) -> tuple[AdapterSection, ...]:
        token = self._source_token(source)
        if (
            type(revision) is not str
            or not revision.strip()
            or type(approved_paths) is not tuple
            or not approved_paths
            or any(type(item) is not ApprovedPath for item in approved_paths)
            or len({item.normalized_path for item in approved_paths}) != len(approved_paths)
            or any(
                item.acquisition_scope is not AcquisitionScope.NATIVE_SECTIONS
                for item in approved_paths
            )
        ):
            raise ContractError("approved Feishu section request is invalid")
        resolved = tuple(
            (
                item,
                self.heading_index.resolve(
                    source.source_id,
                    revision,
                    item.normalized_path,
                ),
            )
            for item in approved_paths
        )
        staged = []
        for approved, block_id in resolved:
            payload = self.runner.fetch_section(token, block_id, revision)
            document = self._document(payload)
            actual_revision = document.get("revision_id")
            if (
                document.get("document_id") != token
                or not (
                    (type(actual_revision) is int and actual_revision >= 0)
                    or (type(actual_revision) is str and bool(actual_revision.strip()))
                )
                or type(document.get("content")) is not str
            ):
                raise ContractError("Feishu section identity or fields are invalid")
            content = document["content"]
            if "<!" in content or "<?" in content:
                raise ContractError("Feishu section XML contains a forbidden construct")
            try:
                root = ET.fromstring(content)
            except ET.ParseError as exc:
                raise ContractError("Feishu section XML is malformed") from exc
            if (
                root.tag != "fragment"
                or root.attrib != {"mode": "section", "requested-start": block_id}
                or len(root)
                or type(root.text) is not str
                or not root.text.strip()
                or not self._whitespace_only(root.tail)
            ):
                raise ContractError("Feishu section fragment is invalid")
            staged.append(
                AdapterSection(
                    source.source_id,
                    approved.normalized_path,
                    str(actual_revision),
                    AcquisitionScope.NATIVE_SECTIONS,
                    root.text,
                )
            )
        return tuple(staged)
