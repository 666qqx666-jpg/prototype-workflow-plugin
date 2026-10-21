"""Bound Standard path context. Never infer identity from cwd or output folders."""
from pathlib import Path, PurePosixPath

from .errors import IntegrityError, PathBoundaryError
from .store import load_json, sha256_file


def workspace_identity(manifest: dict, prototype: Path, workspace: Path | None = None) -> Path:
    source = manifest.get("prototype", {}).get("sourcePath")
    if not isinstance(source, str) or not source:
        raise PathBoundaryError("Manifest sourcePath is required")
    relative = PurePosixPath(source)
    if relative.is_absolute() or ".." in relative.parts or "\\" in source or source != relative.as_posix() or source == ".":
        raise PathBoundaryError("Manifest sourcePath must be normalized and workspace-relative")
    actual = prototype.resolve(strict=True)
    # Rootless historical receipts are recoverable only by exact path suffix,
    # not by searching ancestors, files, cwd, or trusting a caller-selected root.
    if tuple(actual.parts[-len(relative.parts):]) != relative.parts:
        raise IntegrityError("Manifest sourcePath does not identify the bound Prototype")
    inferred = actual.parents[len(relative.parts) - 1]
    root = workspace.resolve(strict=True) if workspace is not None else inferred
    if root != inferred or root == Path(root.anchor):
        raise IntegrityError("Standard workspace differs from the bound sourcePath identity")
    target = root.joinpath(*relative.parts)
    if target.resolve(strict=True) != actual or not actual.is_relative_to(root):
        raise IntegrityError("Prototype escapes its Standard workspace")
    return root


def bound_context(builder: dict, receipt: dict) -> dict:
    """Rehash all receipt entities before granting the same or narrower roots."""
    for key, value in receipt.items():
        if key.endswith("_path") and key != "validator_path":
            hash_key = key[:-5] + "_sha256"
            if hash_key not in receipt:
                continue
            if key == "product_freeze_manifest_path" and value is None:
                # Builder may attach the already supported post-Receipt freeze.
                continue
            if builder.get(key) != value or builder.get(hash_key) != receipt[hash_key]:
                raise IntegrityError(f"Builder differs from Receipt: {key}")
            if value is not None and sha256_file(Path(value)) != receipt[hash_key]:
                raise IntegrityError(f"Standard artifact changed: {key}")
    manifest = load_json(Path(receipt["manifest_path"]))
    context = receipt.get("validation_context")
    if builder.get("validation_context") != context:
        raise IntegrityError("Builder changed the Standard validation context")
    workspace = workspace_identity(manifest, Path(receipt["prototype_path"]),
                                   Path(context["workspace_root"]) if context else None)
    bundle_path = Path(receipt["evidence_path"])
    bundle = load_json(bundle_path)
    if builder["product_freeze_manifest_path"] is not None:
        freeze_path = Path(builder["product_freeze_manifest_path"])
        if sha256_file(freeze_path) != builder["product_freeze_manifest_sha256"]:
            raise IntegrityError("Product Freeze changed")
        freeze = load_json(freeze_path)
        if (freeze.get("validationReceiptSha256") != builder["validation_receipt_sha256"]
                or freeze.get("manifestSha256") != builder["manifest_sha256"]
                or freeze.get("evidenceSha256") != builder["evidence_sha256"]
                or freeze.get("inputs") != {"prdSha256": builder["prd_artifact_sha256"],
                    "prototypeSha256": builder["prototype_sha256"], "readerSha256": builder["reader_snapshot_sha256"]}):
            raise IntegrityError("Product Freeze bindings changed")
    sources = {item["sourceId"]: item for item in manifest["evidenceSources"]}
    roots = set()
    for item in bundle["sources"]:
        declared = sources.get(item["sourceId"])
        if declared is None:
            continue  # Unused bundle entries never grant additional roots.
        path = (bundle_path.parent / item["path"]).resolve(strict=True)
        if sha256_file(path) != declared["sourceHash"]:
            raise IntegrityError("Evidence entity differs from bound Manifest")
        roots.add(str(path.parent))
    if context:
        allowed = [Path(path).resolve(strict=True) for path in context["evidence_roots"]]
        if any(not path.is_dir() for path in allowed) or any(not any(Path(root).is_relative_to(path) for path in allowed) for root in roots):
            raise PathBoundaryError("Evidence escaped the recorded Standard roots")
        roots = {str(path) for path in allowed}
    if not roots:
        raise PathBoundaryError("Standard requires bound evidence roots")
    return {"workspace_root": str(workspace), "evidence_roots": sorted(roots)}
