from __future__ import annotations

from pathlib import Path

from enterprise_prd.canonical import sha256_bytes
from enterprise_prd.contracts import AccessDecision
from enterprise_prd.errors import AccessDenied, ContractError

from .base import AccessAction, SearchHit, SourceDocument, SourceMetadata


class LocalAdapter:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        if not self.root.is_dir():
            raise ContractError("local adapter root must exist")

    def _path(self, document_id: str) -> Path:
        path = (self.root / document_id).resolve()
        if self.root not in path.parents or not path.is_file():
            raise AccessDenied(f"document is outside local scope: {document_id}")
        return path

    def search(self, query: str, *, scope: str) -> tuple[SearchHit, ...]:
        if scope != "project":
            raise ContractError("local adapter only exposes project scope")
        lowered = query.casefold()
        hits = []
        for path in sorted(self.root.glob("*.md")):
            content = path.read_text(encoding="utf-8")
            if lowered in path.stem.casefold() or lowered in content.casefold():
                revision = sha256_bytes(content.encode("utf-8"))
                hits.append(SearchHit(path.name, path.stem, revision, path.as_uri()))
        return tuple(hits)

    def read(self, document_id: str, *, revision: str | None = None) -> SourceDocument:
        path = self._path(document_id)
        content = path.read_text(encoding="utf-8")
        current = sha256_bytes(content.encode("utf-8"))
        if revision is not None and revision != current:
            raise ContractError("requested local revision is not current")
        return SourceDocument(
            document_id=path.name,
            title=path.stem,
            revision=current,
            content=content,
            content_hash=current,
            source_url=path.as_uri(),
            realtime=True,
        )

    def metadata(self, document_id: str) -> SourceMetadata:
        path = self._path(document_id)
        content = path.read_text(encoding="utf-8")
        stat = path.stat()
        return SourceMetadata(
            document_id=path.name,
            title=path.stem,
            revision=sha256_bytes(content.encode("utf-8")),
            source_url=path.as_uri(),
            document_type="markdown",
            author=None,
            modified_at=str(stat.st_mtime_ns),
        )

    def revision(self, document_id: str) -> str:
        return self.metadata(document_id).revision

    def check_access(self, document_id: str, action: AccessAction) -> AccessDecision:
        try:
            self._path(document_id)
        except AccessDenied:
            return AccessDecision.DENIED
        return AccessDecision.ALLOWED if action is AccessAction.READ else AccessDecision.DENIED
