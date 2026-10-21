from __future__ import annotations

import difflib
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.contracts import (
    AccessDecision,
    WriteCommand,
    WritePatch,
    WritePreview,
    WriteResult,
)
from enterprise_prd.errors import AccessDenied, ContractError, RevisionConflict
from enterprise_prd.lark_runner import LarkRunner

from .base import AccessAction, SearchHit, SourceDocument, SourceMetadata
from enterprise_prd.heading_selection import normalize_heading
from enterprise_prd.product_knowledge.models import AdapterHeading, AdapterOutline, AdapterSection


def _xml_fragment(content: str, block_id: str) -> bytes:
    try:
        root = ET.fromstring(f"<root>{content}</root>")
    except ET.ParseError as exc:
        raise ContractError("Feishu XML is malformed") from exc
    matches = [element for element in root.iter() if element.attrib.get("id") == block_id]
    if len(matches) != 1:
        raise ContractError("block_id must resolve to exactly one XML element")
    return ET.tostring(matches[0], encoding="utf-8")


def _outline_heading_elements(root: ET.Element) -> tuple[ET.Element, ...]:
    heading_tags = {f"h{level}" for level in range(1, 7)}
    children = tuple(root)
    if children and all(element.tag in heading_tags for element in children):
        return children
    if len(children) == 1 and children[0].tag == "outline":
        headings = tuple(children[0])
        if any(element.tag not in heading_tags for element in headings):
            raise ContractError("outline may only contain headings")
        return headings
    if any(element.tag == "outline" for element in children):
        raise ContractError("outline fragment must contain a single outline wrapper")
    if children:
        raise ContractError("outline may only contain headings")
    return ()


@dataclass(frozen=True)
class FeishuSourceConfig:
    folder_tokens: tuple[str, ...] = ()
    space_ids: tuple[str, ...] = ()
    document_ids: tuple[str, ...] = ()

    def validate(self) -> None:
        modes = sum(bool(values) for values in (self.folder_tokens, self.space_ids, self.document_ids))
        if modes != 1:
            raise ContractError("configure exactly one source mode")


class FeishuAdapter:
    def __init__(self, config: FeishuSourceConfig, *, runner=None) -> None:
        config.validate()
        self.config = config
        self.runner = runner or LarkRunner()

    def _require_direct_scope(self, document_id: str) -> None:
        if self.config.document_ids and document_id not in self.config.document_ids:
            raise AccessDenied(f"document is outside configured direct scope: {document_id}")

    def search(self, query: str, *, scope: str) -> tuple[SearchHit, ...]:
        if scope != "project":
            raise ContractError("Feishu V0 only exposes project scope")
        if self.config.document_ids:
            raise ContractError("direct document scope does not support broad search")
        arguments = ["drive", "+search", "--query", query, "--page-size", "20"]
        if self.config.folder_tokens:
            arguments += ["--folder-tokens", ",".join(self.config.folder_tokens)]
        else:
            arguments += ["--space-ids", ",".join(self.config.space_ids)]
        payload = self.runner.json(arguments)
        items = payload.get("data", {}).get("items", [])
        if type(items) is not list:
            raise ContractError("Feishu search items must be a list")
        hits = []
        for item in items:
            if type(item) is not dict or item.get("type") not in {"docx", "wiki", "doc"}:
                continue
            token = str(item.get("token", ""))
            title = str(item.get("title", ""))
            url = str(item.get("url", ""))
            if not token or not title or not url:
                raise ContractError("Feishu search item lacks identity")
            hits.append(SearchHit(token, title, str(item.get("revision_id", "")), url))
        return tuple(hits)

    def read(self, document_id: str, *, revision: str | None = None) -> SourceDocument:
        self._require_direct_scope(document_id)
        arguments = [
            "docs", "+fetch", "--doc", document_id,
            "--doc-format", "markdown", "--detail", "simple",
        ]
        if revision is not None:
            arguments += ["--revision-id", revision]
        payload = self.runner.json(arguments)
        document = payload.get("data", {}).get("document")
        if type(document) is not dict:
            raise ContractError("Feishu fetch lacks document")
        content = document.get("content")
        current_revision = document.get("revision_id")
        if type(content) is not str or current_revision is None:
            raise ContractError("Feishu fetch lacks content or revision")
        return SourceDocument(
            document_id=str(document.get("document_id") or document_id),
            title=str(document.get("title") or document_id),
            revision=str(current_revision),
            content=content,
            content_hash=sha256_bytes(content.encode("utf-8")),
            source_url=f"feishu://{document_id}",
            realtime=True,
        )

    def metadata(self, document_id: str) -> SourceMetadata:
        self._require_direct_scope(document_id)
        payload = self.runner.json(
            ["drive", "+inspect", "--url", document_id, "--type", "docx"]
        )
        data = payload.get("data")
        if type(data) is not dict:
            raise ContractError("Feishu inspect lacks metadata")
        token = str(data.get("token") or document_id)
        title = str(data.get("title") or "")
        source_url = str(data.get("url") or "")
        document_type = str(data.get("type") or "")
        if not title or not source_url or not document_type:
            raise ContractError("Feishu metadata lacks required fields")
        return SourceMetadata(
            document_id=token,
            title=title,
            revision=self._revision_probe(document_id),
            source_url=source_url,
            document_type=document_type,
            author=str(data["owner_name"]) if data.get("owner_name") else None,
            modified_at=str(data["modified_time"]) if data.get("modified_time") else None,
        )

    def _revision_probe(self, document_id: str) -> str:
        self._require_direct_scope(document_id)
        payload = self.runner.json([
            "docs", "+fetch", "--doc", document_id,
            "--doc-format", "xml", "--detail", "simple",
            "--scope", "outline", "--max-depth", "0",
        ])
        document = payload.get("data", {}).get("document")
        if type(document) is not dict or document.get("revision_id") is None:
            raise ContractError("Feishu revision probe lacks revision_id")
        return str(document["revision_id"])

    def revision(self, document_id: str) -> str:
        return self._revision_probe(document_id)

    def outline(self, document_id: str) -> AdapterOutline:
        self._require_direct_scope(document_id)
        payload = self.runner.json(["docs", "+fetch", "--doc", document_id, "--doc-format", "xml", "--detail", "with-ids", "--scope", "outline", "--max-depth", "6"])
        document = payload.get("data", {}).get("document")
        if type(document) is not dict or type(document.get("content")) is not str or document.get("revision_id") is None:
            raise ContractError("Feishu outline lacks document content or revision")
        try:
            root = ET.fromstring(document["content"])
        except ET.ParseError as exc:
            raise ContractError("Feishu outline XML is malformed") from exc
        if root.tag != "fragment" or root.attrib.get("mode") != "outline":
            raise ContractError("Feishu outline must be an outline fragment")
        stack: list[tuple[int, str, str]] = []; headings: list[AdapterHeading] = []
        for element in _outline_heading_elements(root):
            block_id = element.attrib.get("id"); raw = "".join(element.itertext()).strip()
            if not block_id or not raw: raise ContractError("outline heading lacks identity")
            level = int(element.tag[1]); normalized = normalize_heading(raw)
            while stack and stack[-1][0] >= level: stack.pop()
            raw_path = "/".join([item[1] for item in stack] + [raw]); normalized_path = "/".join([item[2] for item in stack] + [normalized])
            headings.append(AdapterHeading(block_id, level, raw, normalized, raw_path, normalized_path)); stack.append((level, raw, normalized))
        if not headings or len({item.normalized_path for item in headings}) != len(headings): raise ContractError("outline paths must be unique")
        return AdapterOutline(str(document.get("document_id") or document_id), str(document.get("title") or document_id), str(document["revision_id"]), tuple(headings))

    def read_sections(self, document_id: str, *, revision: str, block_ids: tuple[str, ...]) -> tuple[AdapterSection, ...]:
        self._require_direct_scope(document_id)
        if not block_ids or len(block_ids) != len(set(block_ids)): raise ContractError("section block IDs are invalid")
        sections = []
        for block_id in block_ids:
            payload = self.runner.json(["docs", "+fetch", "--doc", document_id, "--doc-format", "markdown", "--detail", "simple", "--scope", "section", "--start-block-id", block_id, "--revision-id", revision])
            document = payload.get("data", {}).get("document")
            if type(document) is not dict or type(document.get("content")) is not str or str(document.get("revision_id")) != revision or str(document.get("document_id") or document_id) != document_id: raise RevisionConflict("Feishu section returned a different identity or revision")
            content = document["content"]
            try: root = ET.fromstring(content)
            except ET.ParseError: root = None
            if root is not None:
                if root.tag != "fragment" or root.attrib.get("mode") != "section" or root.attrib.get("requested-start") != block_id: raise ContractError("Feishu section fragment is invalid")
                content = "".join(root.itertext())
            sections.append(AdapterSection(document_id, block_id, revision, content, sha256_bytes(content.encode("utf-8"))))
        return tuple(sections)

    def check_access(self, document_id: str, action: AccessAction) -> AccessDecision:
        if self.config.document_ids and document_id not in self.config.document_ids:
            return AccessDecision.DENIED
        if action is AccessAction.WRITE:
            return AccessDecision.UNKNOWN
        try:
            self.runner.json(["drive", "+inspect", "--url", document_id, "--type", "docx"])
        except AccessDenied:
            return AccessDecision.DENIED
        return AccessDecision.ALLOWED

    def _read_xml(self, document_id: str) -> tuple[str, str]:
        self._require_direct_scope(document_id)
        payload = self.runner.json([
            "docs", "+fetch", "--doc", document_id,
            "--doc-format", "xml", "--detail", "with-ids",
        ])
        document = payload.get("data", {}).get("document")
        if type(document) is not dict:
            raise ContractError("Feishu XML fetch lacks document")
        content = document.get("content")
        revision = document.get("revision_id")
        if type(content) is not str or revision is None:
            raise ContractError("Feishu XML fetch lacks content or revision")
        return content, str(revision)

    def preview_write(self, patch: WritePatch) -> WritePreview:
        patch.validate()
        if patch.command is WriteCommand.STR_REPLACE:
            current = self.read(patch.document_id)
            current_revision = current.revision
            if current_revision != patch.expected_revision:
                raise RevisionConflict(
                    f"expected revision {patch.expected_revision}, got {current_revision}"
                )
            assert patch.pattern is not None
            if current.content.count(patch.pattern) != 1:
                raise ContractError("str_replace pattern must match exactly once")
            revised = current.content.replace(patch.pattern, patch.content, 1)
            diff = "\n".join(difflib.unified_diff(
                current.content.splitlines(),
                revised.splitlines(),
                fromfile=f"{patch.document_id}@{current.revision}",
                tofile=f"{patch.document_id}@proposed",
                lineterm="",
            ))
        else:
            assert patch.block_id is not None
            assert patch.expected_fragment_sha256 is not None
            try:
                proposed_root = ET.fromstring(f"<root>{patch.content}</root>")
            except ET.ParseError as exc:
                raise ContractError("block write content must be valid XML fragments") from exc
            if len(proposed_root) == 0:
                raise ContractError("block write content must contain an XML element")
            xml_content, current_revision = self._read_xml(patch.document_id)
            if current_revision != patch.expected_revision:
                raise RevisionConflict(
                    f"expected revision {patch.expected_revision}, got {current_revision}"
                )
            actual_fragment_sha256 = sha256_bytes(
                _xml_fragment(xml_content, patch.block_id)
            )
            if actual_fragment_sha256 != patch.expected_fragment_sha256:
                raise ContractError("block fragment hash does not match current content")
            diff = f"{patch.command.value} block={patch.block_id} content_sha256={sha256_bytes(patch.content.encode('utf-8'))}"
        operation_sha256 = sha256_bytes(canonical_json({
            "document_id": patch.document_id,
            "expected_revision": patch.expected_revision,
            "command": patch.command.value,
            "content": patch.content,
            "pattern": patch.pattern,
            "block_id": patch.block_id,
            "expected_fragment_sha256": patch.expected_fragment_sha256,
        }))
        return WritePreview(patch.document_id, current_revision, operation_sha256, diff)

    def commit_write(self, patch: WritePatch) -> WriteResult:
        self.preview_write(patch)
        arguments = [
            "docs", "+update", "--doc", patch.document_id,
            "--command", patch.command.value,
            "--doc-format", "markdown" if patch.command is WriteCommand.STR_REPLACE else "xml",
            "--content", patch.content,
            "--revision-id", patch.expected_revision,
        ]
        if patch.pattern is not None:
            arguments += ["--pattern", patch.pattern]
        if patch.block_id is not None:
            arguments += ["--block-id", patch.block_id]
        payload = self.runner.json(arguments)
        data = payload.get("data", {})
        if data.get("result") != "success":
            raise ContractError("Feishu update did not complete successfully")
        document = data.get("document", {})
        new_revision = document.get("revision_id")
        count = data.get("updated_blocks_count")
        if new_revision is None or type(count) is not int or count < 1:
            raise ContractError("Feishu update result is incomplete")
        return WriteResult(
            document_id=patch.document_id,
            previous_revision=patch.expected_revision,
            new_revision=str(new_revision),
            updated_blocks_count=count,
        )
