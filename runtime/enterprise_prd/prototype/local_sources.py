"""Explicit local provenance; never falls back from SSH or infers authorization."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from .errors import ContractError, IntegrityError, PathBoundaryError, PrototypeError, RestartRequiredError
from .readiness import load_locked_entry
from .store import safe_run_path, sha256_file, write_once_json

CODE_ROLES = {"host", "component", "style", "interface", "state", "permission", "route"}
CODE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".css", ".less", ".scss", ".sass", ".py", ".json", ".java", ".go", ".cs", ".html"}
AUTH_KEYS = {"schema_version", "kind", "source_root", "files", "authorized_by", "authorized_at", "authorization_evidence"}
CONFIRM_KEYS = {"schema_version", "kind", "workspace_root", "prototype_relative_path", "no_existing_prototype_confirmed", "searched_roots", "confirmed_by", "confirmed_at", "confirmation_evidence"}
CODE_KEYS = {"schema_version", "kind", "authorization_path", "authorization_sha256", "source_root", "files"}
FIRST_KEYS = {"schema_version", "kind", "confirmation_path", "confirmation_sha256", "workspace_root", "prototype_relative_path"}
COLLECTION_KEYS = {"schema_version", "kind", "sources"}


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ContractError("duplicate provenance JSON field")
        value[key] = item
    return value


def _read(path: Path):
    try:
        payload = path.read_bytes()
        value = json.loads(payload, object_pairs_hook=_pairs)
    except (OSError, UnicodeError, ValueError) as exc:
        raise IntegrityError("cannot read local provenance record") from exc
    return value, hashlib.sha256(payload).hexdigest()


def _closed(value, keys, kind):
    if type(value) is not dict or set(value) != keys or type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != kind:
        raise ContractError("local provenance contract is invalid")


def _text(value):
    if type(value) is not str or not value.strip():
        raise ContractError("local provenance requires explicit confirmation")


def _root(value):
    _text(value)
    raw = Path(value)
    root = raw.resolve()
    if not raw.is_absolute() or raw.is_symlink() or not root.is_dir() or root in {Path("/"), Path.home().resolve()}:
        raise PathBoundaryError("a specific existing project root is required")
    return root


def _relative(value, *, allow_dot=False):
    _text(value)
    path = PurePosixPath(value)
    if "\\" in value or path.is_absolute() or ".." in path.parts or (value != path.as_posix()) or (value == "." and not allow_dot):
        raise PathBoundaryError("source path must be a normalized project-relative path")
    return path


def _inside(root, value, *, allow_dot=False):
    relative = _relative(value, allow_dot=allow_dot)
    path = root.joinpath(*relative.parts)
    current = path
    while current != root:
        if current.is_symlink():
            raise PathBoundaryError("symlinked local source paths are not allowed")
        current = current.parent
    try:
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise PathBoundaryError("local source escapes its authorized root") from exc
    return path


def _proof(value):
    if type(value) is not dict or set(value) != {"path", "sha256"}:
        raise ContractError("confirmation must bind its actual evidence file")
    _text(value["path"])
    path = Path(value["path"])
    if not path.is_absolute() or path.suffix.lower() not in {".md", ".txt", ".json"} or any(part in {".ssh", ".config"} for part in path.parts):
        raise PathBoundaryError("confirmation evidence must be an explicit non-secret task material")
    if sha256_file(path) != value["sha256"]:
        raise IntegrityError("local source confirmation evidence changed")


def _authorized_files(authorization):
    _closed(authorization, AUTH_KEYS, "local-code-authorization")
    _text(authorization["authorized_by"])
    _text(authorization["authorized_at"])
    _proof(authorization["authorization_evidence"])
    root = _root(authorization["source_root"])
    if type(authorization["files"]) is not list or not authorization["files"]:
        raise ContractError("local code requires an explicit nonempty file list")
    locked, seen, roles = [], set(), set()
    for item in authorization["files"]:
        if type(item) is not dict or set(item) != {"path", "roles"} or type(item["roles"]) is not list or not item["roles"] or any(type(role) is not str for role in item["roles"]):
            raise ContractError("local code file selection is invalid")
        file_roles = set(item["roles"])
        if len(file_roles) != len(item["roles"]) or not file_roles <= CODE_ROLES:
            raise ContractError("local code roles are invalid")
        path = _inside(root, item["path"])
        if item["path"] in seen:
            raise ContractError("duplicate local code path")
        seen.add(item["path"])
        if not path.is_file() or path.suffix.lower() not in CODE_EXTENSIONS or any(part.startswith(".") or part == "node_modules" for part in PurePosixPath(item["path"]).parts):
            raise PathBoundaryError("local code selection is missing or not a permitted source file")
        payload = path.read_bytes()
        try:
            payload.decode("utf-8")
        except UnicodeError as exc:
            raise ContractError("local code must be UTF-8 source") from exc
        if b"BEGIN PRIVATE" + b" KEY" in payload or b"BEGIN OPENSSH PRIVATE" + b" KEY" in payload:
            raise ContractError("private key material is not code evidence")
        locked.append({"path": item["path"], "roles": item["roles"], "sha256": hashlib.sha256(payload).hexdigest()})
        roles.update(file_roles)
    if roles != CODE_ROLES:
        raise ContractError("local code selection must cover host, components, style, interface, state, permission and route")
    return root, locked


def _first_confirmation(confirmation, *, require_absent=False, prototype_path=None):
    _closed(confirmation, CONFIRM_KEYS, "first-prototype-confirmation")
    _text(confirmation["confirmed_by"])
    _text(confirmation["confirmed_at"])
    _proof(confirmation["confirmation_evidence"])
    if confirmation["no_existing_prototype_confirmed"] is not True:
        raise ContractError("first generation needs an explicit no-existing-prototype confirmation")
    root = _root(confirmation["workspace_root"])
    target = _inside(root, confirmation["prototype_relative_path"])
    if target.suffix.lower() != ".html":
        raise ContractError("first prototype target must be an HTML source")
    searches = confirmation["searched_roots"]
    if type(searches) is not list or not searches or any(type(item) is not str for item in searches) or len(set(searches)) != len(searches):
        raise ContractError("first generation must record its scoped source search")
    if any(not _inside(root, item, allow_dot=True).is_dir() for item in searches):
        raise PathBoundaryError("prototype source search root is invalid")
    if require_absent and target.exists():
        raise IntegrityError("first generation cannot replace an existing prototype target")
    if prototype_path is not None and Path(prototype_path).resolve() != target.resolve():
        raise IntegrityError("first prototype output differs from the confirmed target")
    return root, target


def capture_local_code(*, run_root: Path, entry_path: Path, authorization_path: Path) -> Path:
    entry, entry_hash = load_locked_entry(entry_path)
    authorization, authorization_hash = _read(authorization_path)
    root, files = _authorized_files(authorization)
    document = {"schema_version": 1, "kind": "authorized-local-code",
        "authorization_path": str(authorization_path.resolve()), "authorization_sha256": authorization_hash,
        "source_root": str(root), "files": files}
    output = safe_run_path(run_root, entry["prototype_run_id"], "local-code-source.json")
    def check():
        load_locked_entry(entry_path, entry_hash)
        _validate_record(document, "current-code")
    write_once_json(output, document, binding_check=check)
    return output


def capture_first_baseline(*, run_root: Path, entry_path: Path, confirmation_path: Path) -> Path:
    entry, entry_hash = load_locked_entry(entry_path)
    confirmation, confirmation_hash = _read(confirmation_path)
    root, _ = _first_confirmation(confirmation, require_absent=True)
    document = {"schema_version": 1, "kind": "first-prototype-baseline",
        "confirmation_path": str(confirmation_path.resolve()), "confirmation_sha256": confirmation_hash,
        "workspace_root": str(root), "prototype_relative_path": confirmation["prototype_relative_path"]}
    output = safe_run_path(run_root, entry["prototype_run_id"], "first-prototype-baseline.json")
    def check():
        load_locked_entry(entry_path, entry_hash)
        _validate_record(document, "current-prototype-baseline", require_absent=True)
    write_once_json(output, document, binding_check=check)
    return output


def capture_code_collection(*, run_root: Path, entry_path: Path, source_paths: list[Path]) -> Path:
    entry, entry_hash = load_locked_entry(entry_path)
    paths = [path.resolve() for path in source_paths]
    if not paths or len(set(paths)) != len(paths):
        raise ContractError("code collection requires unique captured source paths")
    document = {"schema_version": 1, "kind": "code-source-collection",
        "sources": [{"path": str(path), "sha256": sha256_file(path)} for path in paths]}
    output = safe_run_path(run_root, entry["prototype_run_id"], "code-source-collection.json")
    def check():
        load_locked_entry(entry_path, entry_hash)
        _validate_record(document, "current-code")
    write_once_json(output, document, binding_check=check)
    return output


def _validate_record(document, role, *, require_absent=False, prototype_path=None):
    if document.get("kind") == "code-source-collection":
        if role != "current-code":
            raise ContractError("code collection has the wrong source role")
        _closed(document, COLLECTION_KEYS, "code-source-collection")
        if type(document["sources"]) is not list or not document["sources"]:
            raise ContractError("code collection is empty")
        seen, local_count = set(), 0
        for source in document["sources"]:
            if type(source) is not dict or set(source) != {"path", "sha256"}:
                raise ContractError("code collection child is invalid")
            path = Path(source["path"])
            if not path.is_absolute() or path.resolve() in seen or path.suffix.lower() not in CODE_EXTENSIONS | {".md", ".txt"} or any(part in {".ssh", ".config"} for part in path.parts):
                raise PathBoundaryError("code collection child is unsafe or duplicated")
            seen.add(path.resolve())
            if sha256_file(path) != source["sha256"]:
                raise IntegrityError("captured code collection member changed")
            if path.read_bytes().lstrip().startswith(b"{"):
                child, _ = _read(path)
                if type(child) is dict and child.get("kind") == "code-source-collection":
                    raise ContractError("nested code source collections are not supported")
            child = validate_source_record(path, "current-code")
            local_count += bool(child and child.get("kind") == "authorized-local-code")
        if local_count == 0:
            raise ContractError("local/remote collection needs an explicitly authorized local capture")
    elif document.get("kind") == "authorized-local-code":
        if role != "current-code":
            raise ContractError("local code record has the wrong source role")
        _closed(document, CODE_KEYS, "authorized-local-code")
        authorization, digest = _read(Path(document["authorization_path"]))
        root, files = _authorized_files(authorization)
        if digest != document["authorization_sha256"] or str(root) != document["source_root"] or files != document["files"]:
            raise IntegrityError("authorized local code or its confirmation changed")
    elif document.get("kind") == "first-prototype-baseline":
        if role != "current-prototype-baseline":
            raise ContractError("first prototype declaration has the wrong source role")
        _closed(document, FIRST_KEYS, "first-prototype-baseline")
        confirmation, digest = _read(Path(document["confirmation_path"]))
        root, _ = _first_confirmation(confirmation, require_absent=require_absent, prototype_path=prototype_path)
        if digest != document["confirmation_sha256"] or str(root) != document["workspace_root"] or confirmation["prototype_relative_path"] != document["prototype_relative_path"]:
            raise IntegrityError("first prototype confirmation changed")


def validate_source_record(path: Path, role: str, *, require_absent=False, prototype_path=None):
    # Ordinary frozen code/HTML sources retain their existing contract. Only the
    # two explicit structured provenance kinds activate this additional adapter.
    payload = path.read_bytes()
    if payload.lstrip().startswith(b"{"):
        value, _ = _read(path)
        if type(value) is dict and value.get("kind") in {"authorized-local-code", "first-prototype-baseline", "code-source-collection"}:
            _validate_record(value, role, require_absent=require_absent, prototype_path=prototype_path)
            return value
    return None


def validate_source_set_records(source_set, *, require_absent=False, prototype_path=None):
    for source in source_set["sources"]:
        path = Path(source["path"])
        if sha256_file(path) != source["sha256"]:
            raise IntegrityError("locked local source changed")
        validate_source_record(path, source["role"], require_absent=require_absent,
            prototype_path=prototype_path if source["role"] == "current-prototype-baseline" else None)


def assert_locked_sources(snapshot, *, prototype_path=None):
    try:
        source_set, digest = _read(Path(snapshot["source_set_path"]))
        if digest != snapshot["source_set_sha256"]:
            raise IntegrityError("Source Set changed")
        validate_source_set_records(source_set, prototype_path=prototype_path)
    except (OSError, ValueError, PrototypeError) as exc:
        raise RestartRequiredError("locked prototype sources changed during the run") from exc


def assert_standard_source_binding(source_set, manifest_path: Path, evidence_path: Path):
    manifest, _ = _read(manifest_path)
    evidence, _ = _read(evidence_path)
    source_by_id = {item["sourceId"]: item for item in manifest.get("evidenceSources", [])}
    files_by_id = {item["sourceId"]: item for item in evidence.get("sources", [])}
    def evidence_file(source_id):
        raw = files_by_id.get(source_id, {}).get("path")
        if type(raw) is not str:
            raise IntegrityError("Standard omits a locked source file")
        path = Path(raw)
        return (path if path.is_absolute() else evidence_path.parent / path).resolve()
    def local_files(document):
        if document and document["kind"] == "code-source-collection":
            return [item for source in document["sources"] for item in local_files(
                validate_source_record(Path(source["path"]), "current-code"))]
        if document and document["kind"] == "authorized-local-code":
            return [(Path(document["source_root"]) / item["path"], item["sha256"]) for item in document["files"]]
        return []
    for source in source_set["sources"]:
        document = validate_source_record(Path(source["path"]), source["role"])
        if document and document["kind"] == "first-prototype-baseline":
            prototype = manifest.get("prototype", {})
            source_id = prototype.get("baselineSourceId")
            if prototype.get("baselineKind") != "first-generation" or prototype.get("baselineHash") != source["sha256"] or source_by_id.get(source_id, {}).get("sourceHash") != source["sha256"] or evidence_file(source_id) != Path(source["path"]).resolve():
                raise IntegrityError("Standard must bind the Run's exact first-prototype declaration")
        if source["role"] == "current-code":
            for expected_path, expected_hash in local_files(document):
                if not any(item.get("sourceType") == "code" and item.get("sourceHash", "").lower() == expected_hash and evidence_file(source_id) == expected_path.resolve()
                    for source_id, item in source_by_id.items()):
                    raise IntegrityError("Standard must include every actual authorized local code file")
