from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from enterprise_prd.errors import ContractError
from enterprise_prd.heading_selection import normalize_heading

_HEADING = re.compile(r"^ {0,3}(#{1,6}) +(.*)$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


@dataclass(frozen=True)
class MarkdownHeading:
    title: str
    normalized_title: str
    level: int
    raw_path: str
    normalized_path: str
    start: int
    end: int


@dataclass(frozen=True)
class ParsedMarkdown:
    text: str
    title: str
    headings: tuple[MarkdownHeading, ...]

    def by_normalized_path(self, path: str) -> MarkdownHeading:
        matches = tuple(item for item in self.headings if item.normalized_path == path)
        if len(matches) != 1:
            raise ContractError("snapshot heading path must match exactly once")
        return matches[0]

    def overlaps(self, paths: tuple[str, ...]) -> bool:
        ordered = tuple(dict.fromkeys(paths))
        if len(ordered) != len(paths):
            return True
        return any(
            right.startswith(left + "/") or left.startswith(right + "/")
            for index, left in enumerate(ordered)
            for right in ordered[index + 1 :]
        )

    def extract(self, paths: tuple[str, ...]) -> tuple[str, ...]:
        if type(paths) is not tuple or not paths or len(paths) != len(set(paths)):
            raise ContractError("snapshot approved paths are invalid")
        if self.overlaps(paths):
            raise ContractError("snapshot approved sections overlap")
        return tuple(self.text[item.start : item.end] for item in (self.by_normalized_path(path) for path in paths))


def _title(raw: str) -> tuple[str, str]:
    value = raw.rstrip()
    closing = re.search(r" +#+ *$", value)
    if closing is not None:
        value = value[: closing.start()].rstrip()
    title = value.strip()
    if not title or "/" in title or any(unicodedata.category(char).startswith("C") for char in title):
        raise ContractError("snapshot ATX heading title is invalid")
    normalized = normalize_heading(title)
    if not normalized:
        raise ContractError("snapshot normalized heading is empty")
    return title, normalized


def parse_markdown(text: str) -> ParsedMarkdown:
    if type(text) is not str or not text or "\r" in text:
        raise ContractError("snapshot markdown text is invalid")
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line)
    stack: list[tuple[int, str, str]] = []
    parsed: list[tuple[str, str, int, str, str, int]] = []
    fence: tuple[str, int] | None = None
    first_nonempty_offset = next((offsets[index] for index, line in enumerate(lines) if line.strip()), None)
    for index, line in enumerate(lines):
        bare = line.rstrip("\n")
        fence_match = _FENCE.match(bare)
        if fence is not None:
            if fence_match is not None and fence_match.group(1)[0] == fence[0] and len(fence_match.group(1)) >= fence[1] and not fence_match.group(2).strip():
                fence = None
            continue
        if fence_match is not None:
            marker = fence_match.group(1)
            fence = (marker[0], len(marker))
            continue
        match = _HEADING.fullmatch(bare)
        if match is None:
            continue
        level = len(match.group(1))
        title, normalized_title = _title(match.group(2))
        while stack and stack[-1][0] >= level:
            stack.pop()
        raw_path = "/".join([item[1] for item in stack] + [title])
        normalized_path = "/".join([item[2] for item in stack] + [normalized_title])
        parsed.append((title, normalized_title, level, raw_path, normalized_path, offsets[index]))
        stack.append((level, title, normalized_title))
    if not parsed or first_nonempty_offset != parsed[0][5]:
        raise ContractError("snapshot first content must be an ATX heading")
    raw_paths = [item[3] for item in parsed]
    normalized_paths = [item[4] for item in parsed]
    if len(raw_paths) != len(set(raw_paths)) or len(normalized_paths) != len(set(normalized_paths)):
        raise ContractError("snapshot heading paths must be unique")
    headings: list[MarkdownHeading] = []
    for index, item in enumerate(parsed):
        end = len(text)
        for candidate in parsed[index + 1 :]:
            if candidate[2] <= item[2]:
                end = candidate[5]
                break
        headings.append(MarkdownHeading(*item[:5], item[5], end))
    document_title = next((item.title for item in headings if item.level == 1), headings[0].title)
    return ParsedMarkdown(text, document_title, tuple(headings))
