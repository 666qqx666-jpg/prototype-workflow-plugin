from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .contracts import _identifier, _sha256, _text, validate_entry
from .errors import ContractError, RestartRequiredError

ASSESSMENT_KEYS = frozenset({"schema_version", "prd_artifact_sha256", "review_artifact_sha256",
    "review_scope", "review_evidence", "prototype_readiness", "spec_readiness", "reason",
    "blocking_item_ids", "pending_interactions"})
PENDING_KEYS = frozenset({"id", "source_locator", "question", "product_constraints", "expected_output"})

def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ContractError("readiness JSON has duplicate fields")
        value[key] = item
    return value

def _reject_constant(value):
    raise ContractError("readiness JSON has non-finite values")

def _read_document(path):
    try:
        payload = Path(path).read_bytes()
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RestartRequiredError("readiness input is unavailable; create a new Run") from error
    return value, hashlib.sha256(payload).hexdigest()

def validate_assessment(value):
    if type(value) is not dict or set(value) != ASSESSMENT_KEYS:
        raise ContractError("prototype readiness fields are invalid")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("prototype readiness schema_version must be 1")
    for key in ("prd_artifact_sha256", "review_artifact_sha256"):
        _sha256(value[key], key)
    for key, choices in (("review_scope", {"self_review", "independent_review"}),
        ("review_evidence", {"pass", "block"}), ("prototype_readiness", {"pass", "block"}),
        ("spec_readiness", {"pass", "block", "not_assessed"})):
        if type(value[key]) is not str or value[key] not in choices:
            raise ContractError(f"{key} is invalid")
    _text(value["reason"], "reason", maximum=4096)
    ids = value["blocking_item_ids"]
    if type(ids) is not list:
        raise ContractError("blocking_item_ids must be a list")
    for item in ids:
        _identifier(item, "blocking item ID")
    if len(ids) != len(set(ids)) or (value["prototype_readiness"] == "pass" and ids):
        raise ContractError("prototype readiness contradicts its blockers")
    pending = value["pending_interactions"]
    if type(pending) is not list:
        raise ContractError("pending_interactions must be a list")
    pending_ids = []
    for item in pending:
        if type(item) is not dict or set(item) != PENDING_KEYS:
            raise ContractError("pending interaction fields are invalid")
        pending_ids.append(_identifier(item["id"], "pending interaction ID"))
        for key in PENDING_KEYS - {"id"}:
            _text(item[key], key, maximum=4096)
    if len(pending_ids) != len(set(pending_ids)) or set(pending_ids) & set(ids):
        raise ContractError("interaction IDs are duplicated or also blocking")
    return value

def load_assessment(path):
    value, digest = _read_document(path)
    return validate_assessment(value), digest

def assert_entry_binding(entry):
    validate_entry(entry)
    record, digest = load_assessment(entry["readiness_assessment_path"])
    if digest != entry["readiness_assessment_sha256"]:
        raise RestartRequiredError("prototype readiness changed; create a new Run")
    for key in ("review_evidence", "prototype_readiness", "spec_readiness",
                "prd_artifact_sha256", "review_artifact_sha256"):
        if record[key] != entry[key]:
            raise RestartRequiredError("prototype readiness differs from Entry")
    for prefix in ("prd", "review"):
        try:
            digest = hashlib.sha256(Path(entry[f"{prefix}_artifact_path"]).read_bytes()).hexdigest()
        except OSError as error:
            raise RestartRequiredError("prototype entry input is unavailable") from error
        if digest != entry[f"{prefix}_artifact_sha256"]:
            raise RestartRequiredError("prototype entry input changed; create a new Run")
    if record["blocking_item_ids"]:
        raise ContractError("prototype readiness has blockers")
    return record

def load_locked_entry(path, expected_sha256=None):
    entry, digest = _read_document(path)
    if type(entry) is dict and type(entry.get("schema_version")) is int and entry["schema_version"] == 1:
        raise RestartRequiredError("Prototype Entry v1 cannot resume; create an assessment and a new Run; do not change NOT_ASSESSED to PASS")
    if expected_sha256 is not None and digest != expected_sha256:
        raise RestartRequiredError("Prototype Entry changed; create a new Run")
    assert_entry_binding(entry)
    return entry, digest
