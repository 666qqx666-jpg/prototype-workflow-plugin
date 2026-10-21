from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import unicodedata

from .errors import ContractError


_HEADING = re.compile(r"^( {0,3})(#{1,6})(?:[ \t]+(.*)|[ \t]*)$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


@dataclass(frozen=True)
class ExtractedSection:
    heading_path: str
    heading_level: int
    start: int
    end: int
    content: str
    content_sha256: str
    selected_chars: int


@dataclass(frozen=True)
class _Heading:
    level: int
    title: str
    path: str
    start: int
    end: int
    line_index: int


def normalize_heading(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    return " ".join(normalized.split())


def _title(raw: str) -> str:
    value = raw.rstrip()
    closing = re.search(r"[ \t]+#+[ \t]*$", value)
    if closing is not None:
        value = value[:closing.start()].rstrip()
    title = normalize_heading(value)
    if not title or "/" in title:
        raise ContractError("ATX heading title is invalid")
    return title


def _parse(content: str) -> tuple[tuple[_Heading, ...], tuple[str, ...], tuple[bool, ...], tuple[int, ...]]:
    lines = content.splitlines(keepends=True)
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line)
    headings: list[_Heading] = []
    visible: list[bool] = []
    stack: list[_Heading] = []
    fence: tuple[str, int] | None = None
    for index, line in enumerate(lines):
        fence_match = _FENCE.match(line)
        if fence is not None:
            visible.append(False)
            if fence_match is not None and fence_match.group(1)[0] == fence[0] and len(fence_match.group(1)) >= fence[1]:
                fence = None
            continue
        if fence_match is not None:
            visible.append(False)
            marker = fence_match.group(1)
            fence = (marker[0], len(marker))
            continue
        visible.append(True)
        match = _HEADING.match(line.rstrip("\r\n"))
        if match is None:
            continue
        level = len(match.group(2))
        title = _title(match.group(3) or "")
        while stack and stack[-1].level >= level:
            stack.pop()
        parent = stack[-1].path if stack and stack[-1].level > 1 else ""
        heading = _Heading(
            level=level,
            title=title,
            path=f"{parent}/{title}" if parent else title,
            start=offsets[index],
            end=offsets[index] + len(line),
            line_index=index,
        )
        headings.append(heading)
        stack.append(heading)
    return tuple(headings), tuple(lines), tuple(visible), tuple(offsets)


def _validate_requests(heading_paths: tuple[str, ...]) -> tuple[str, ...]:
    if type(heading_paths) is not tuple or not heading_paths:
        raise ContractError("heading paths must be a non-empty tuple")
    normalized: list[str] = []
    for path in heading_paths:
        if type(path) is not str or not path.strip():
            raise ContractError("heading path is invalid")
        parts = path.split("/")
        if any(not part.strip() for part in parts):
            raise ContractError("heading path hierarchy is invalid")
        value = "/".join(normalize_heading(part) for part in parts)
        normalized.append(value)
    if len(normalized) != len(set(normalized)):
        raise ContractError("heading paths must be unique")
    return tuple(normalized)


def extract_markdown_sections(content: str, heading_paths: tuple[str, ...]) -> tuple[ExtractedSection, ...]:
    """Exact, case-sensitive ATX path matching after NFKC/space normalization."""
    if type(content) is not str:
        raise ContractError("markdown content is invalid")
    paths = _validate_requests(heading_paths)
    headings, lines, visible, offsets = _parse(content)
    selected: list[tuple[_Heading, int]] = []
    for path in paths:
        matches = [heading for heading in headings if heading.path == path]
        if len(matches) != 1:
            raise ContractError(f"heading path must match exactly once: {path}")
        heading = matches[0]
        end = len(content)
        for candidate in headings:
            if candidate.start > heading.start and candidate.level <= heading.level:
                end = candidate.start
                break
        selected.append((heading, end))
    selected.sort(key=lambda item: item[0].start)
    non_overlapping: list[tuple[_Heading, int]] = []
    for heading, end in selected:
        if non_overlapping and heading.start < non_overlapping[-1][1]:
            continue
        non_overlapping.append((heading, end))
    result: list[ExtractedSection] = []
    for heading, end in non_overlapping:
        end_line = next((i for i, offset in enumerate(offsets) if offset >= end), len(lines))
        rendered = "".join(
            line for index, line in enumerate(lines[heading.line_index:end_line], start=heading.line_index)
            if visible[index]
        )
        digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        result.append(ExtractedSection(heading.path, heading.level, heading.start, end, rendered, digest, len(rendered)))
    return tuple(result)
