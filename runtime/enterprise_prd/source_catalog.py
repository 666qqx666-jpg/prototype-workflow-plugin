from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .canonical import load_yaml, sha256_file
from .contracts import IDENTITY_RE, need
from .errors import ContractError
from .product_knowledge.models import Lifecycle


@dataclass(frozen=True)
class SourceLocator:
    adapter: str
    token: str


@dataclass(frozen=True)
class SourceClassification:
    product_domain: str
    module: str
    material_type: str


@dataclass(frozen=True)
class SourceGovernance:
    lifecycle: Lifecycle
    superseded_by: tuple[str, ...]


@dataclass(frozen=True)
class SourceTarget:
    source_id: str
    kind: str
    locator: SourceLocator
    classification: SourceClassification
    governance: SourceGovernance

    @property
    def token(self) -> str:
        return self.locator.token

    def validate(self) -> None:
        need(type(self.source_id) is str and IDENTITY_RE.fullmatch(self.source_id) is not None, "source_id is invalid")
        need(self.kind == "document", "source kind is invalid")
        need(type(self.locator.adapter) is str and bool(self.locator.adapter), "source adapter is invalid")
        need(type(self.locator.token) is str and bool(self.locator.token.strip()), "source locator token is invalid")
        need(all(type(value) is str and bool(value.strip()) for value in (self.classification.product_domain, self.classification.module, self.classification.material_type)), "source classification is invalid")
        need(type(self.governance.lifecycle) is Lifecycle, "source lifecycle is invalid")
        need(type(self.governance.superseded_by) is tuple and all(type(item) is str and bool(item) for item in self.governance.superseded_by), "source superseded_by is invalid")
        need(len(self.governance.superseded_by) == len(set(self.governance.superseded_by)), "source superseded_by has duplicates")


@dataclass(frozen=True)
class SourcePolicy:
    mode: str
    allowed_adapters: tuple[str, ...]
    local_catalog: str
    registry_schema: int
    selection_protocol: str
    outline_requires_document_confirmation: bool
    body_requires_read_grant: bool


def load_source_policy(pack_root: Path) -> SourcePolicy:
    raw = load_yaml(pack_root.resolve() / "sources/catalog.yaml")
    expected = {"schema_version", "mode", "allowed_adapters", "local_catalog", "registry_schema", "selection_protocol", "outline_requires_document_confirmation", "body_requires_read_grant"}
    if set(raw) != expected:
        raise ContractError("source policy fields are invalid")
    allowed = raw["allowed_adapters"]
    local_name = raw["local_catalog"]
    if (raw["schema_version"] != 3 or raw["mode"] != "requires-scoped-local-registry" or type(allowed) is not list or not allowed or any(type(item) is not str or not item for item in allowed) or len(allowed) != len(set(allowed)) or type(local_name) is not str or PurePosixPath(local_name).name != local_name or raw["registry_schema"] != 2 or raw["selection_protocol"] != "context-selection/v1" or raw["outline_requires_document_confirmation"] is not True or raw["body_requires_read_grant"] is not True):
        raise ContractError("source policy is invalid")
    return SourcePolicy(raw["mode"], tuple(allowed), local_name, 2, raw["selection_protocol"], True, True)


@dataclass(frozen=True)
class SourceCatalog:
    path: Path
    identity: str
    targets: tuple[SourceTarget, ...]
    sha256: str
    schema_version: int = 2

    @property
    def sources(self) -> tuple[SourceTarget, ...]:
        return self.targets

    def resolve(self, source_id: str) -> SourceTarget:
        matches = tuple(item for item in self.targets if item.source_id == source_id)
        if len(matches) != 1:
            raise ContractError(f"source ID is outside approved registry: {source_id}")
        return matches[0]


def _target(raw: object) -> SourceTarget:
    expected = {"source_id", "kind", "locator", "classification", "governance"}
    if type(raw) is not dict or set(raw) != expected:
        raise ContractError("source fields are invalid")
    locator, classification, governance = raw["locator"], raw["classification"], raw["governance"]
    if type(locator) is not dict or set(locator) != {"adapter", "token"} or type(classification) is not dict or set(classification) != {"product_domain", "module", "material_type"} or type(governance) is not dict or set(governance) != {"lifecycle", "superseded_by"} or type(governance["superseded_by"]) is not list:
        raise ContractError("source fields are invalid")
    try:
        lifecycle = Lifecycle(governance["lifecycle"])
    except ValueError as exc:
        raise ContractError("source lifecycle is invalid") from exc
    value = SourceTarget(raw["source_id"], raw["kind"], SourceLocator(locator["adapter"], locator["token"]), SourceClassification(classification["product_domain"], classification["module"], classification["material_type"]), SourceGovernance(lifecycle, tuple(governance["superseded_by"])))
    value.validate()
    return value


def load_source_catalog(path: Path, pack_root: Path) -> SourceCatalog:
    resolved = path.expanduser().resolve()
    raw = load_yaml(resolved)
    if set(raw) != {"schema_version", "identity", "sources"} or raw["schema_version"] != 2 or raw["identity"] != "user" or type(raw["sources"]) is not list or not raw["sources"]:
        raise ContractError("local source registry fields are invalid")
    policy = load_source_policy(pack_root)
    if resolved.name != policy.local_catalog:
        raise ContractError("source policy does not allow this local registry")
    targets = tuple(_target(item) for item in raw["sources"])
    if len({item.source_id for item in targets}) != len(targets) or len({(item.locator.adapter, item.token) for item in targets}) != len(targets):
        raise ContractError("source IDs and locators must each be unique")
    ids = {item.source_id for item in targets}
    for item in targets:
        if item.locator.adapter not in policy.allowed_adapters:
            raise ContractError("source adapter is not allowed")
        targets_for_item = item.governance.superseded_by
        if item.governance.lifecycle is Lifecycle.SUPERSEDED:
            if not targets_for_item or item.source_id in targets_for_item or not set(targets_for_item) <= ids:
                raise ContractError("superseded source requires existing non-self target")
        elif targets_for_item:
            raise ContractError("only superseded source may have replacements")
    graph = {item.source_id: item.governance.superseded_by for item in targets}
    def visit(node: str, visiting: set[str], done: set[str]) -> None:
        if node in visiting: raise ContractError("superseded_by has a cycle")
        if node in done: return
        visiting.add(node)
        for child in graph[node]: visit(child, visiting, done)
        visiting.remove(node); done.add(node)
    done: set[str] = set()
    for node in graph: visit(node, set(), done)
    return SourceCatalog(resolved, "user", targets, sha256_file(resolved))
