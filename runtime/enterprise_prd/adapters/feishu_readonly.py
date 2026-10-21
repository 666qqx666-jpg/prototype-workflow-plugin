from __future__ import annotations

from enterprise_prd.contracts import AccessDecision
from enterprise_prd.errors import ContractError, RevisionConflict

from .base import (
    AccessAction,
    SearchHit,
    SourceDocument,
    SourceMetadata,
)
from .feishu import FeishuAdapter, FeishuSourceConfig
from enterprise_prd.product_knowledge.models import AdapterOutline, AdapterSection


class FeishuReadOnlyBridge:
    __slots__ = ("_reader",)

    def __init__(self, document_ids: tuple[str, ...], *, runner=None) -> None:
        self._reader = FeishuAdapter(
            FeishuSourceConfig(document_ids=document_ids),
            runner=runner,
        )

    def search(self, query: str, *, scope: str) -> tuple[SearchHit, ...]:
        raise ContractError(
            "read-only bridge requires explicit catalog sources"
        )

    def metadata(self, document_id: str) -> SourceMetadata:
        return self._reader.metadata(document_id)

    def revision(self, document_id: str) -> str:
        return self._reader.revision(document_id)

    def read(
        self,
        document_id: str,
        *,
        revision: str | None = None,
    ) -> SourceDocument:
        locked_revision = (
            revision if revision is not None else self.revision(document_id)
        )
        document = self._reader.read(
            document_id,
            revision=locked_revision,
        )
        if (
            document.document_id != document_id
            or document.revision != locked_revision
        ):
            raise RevisionConflict(
                "Feishu read did not return the locked document revision"
            )
        return document

    def check_access(
        self,
        document_id: str,
        action: AccessAction,
    ) -> AccessDecision:
        if action is AccessAction.WRITE:
            return AccessDecision.DENIED
        return self._reader.check_access(document_id, action)

    def outline(self, document_id: str) -> AdapterOutline:
        return self._reader.outline(document_id)

    def read_sections(self, document_id: str, *, revision: str, block_ids: tuple[str, ...]) -> tuple[AdapterSection, ...]:
        return self._reader.read_sections(document_id, revision=revision, block_ids=block_ids)
