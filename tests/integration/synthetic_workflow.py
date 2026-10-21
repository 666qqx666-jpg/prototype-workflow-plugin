"""Synthetic native Standard closure, shared by source and final installed-kit tests.

Only fixture authoring is synthetic. invoke executes the real CLI; validators and
Chromium are never mocked. No business data or business review claim is involved.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

NOW = "2026-10-19T08:00:00+08:00"
CHECKS = ["unrelated-field-preservation", "empty-and-missing-values",
          "historical-out-of-candidate-data", "unauthorized-actions",
          "concurrent-conflict", "post-save-membership"]
CORE = ["change-overview", "collapse-expand-all", "return-previous-prototype",
        "full-development-snapshot", "change-kind-color", "current-location",
        "bidirectional-visible-connector", "fact-source-details"]


class NativeCLI:
    def __init__(self, prefix, cwd, log_root, env=None):
        self.prefix, self.cwd, self.log_root = prefix, cwd, log_root
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", **(env or {}))
        self.count = 0
        cwd.mkdir(parents=True, exist_ok=True)
        log_root.mkdir(parents=True, exist_ok=True)

    def __call__(self, *args, blocked=False):
        self.count += 1
        argv = [*map(str, self.prefix), "prototype", *map(str, args)]
        result = subprocess.run(argv, cwd=self.cwd, env=self.env, text=True, capture_output=True, timeout=180)
        save(self.log_root / f"{self.count:03}-{args[0]}.json", {"argv": argv, "cwd": str(self.cwd),
             "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        if blocked:
            assert result.returncode != 0, result.stdout
            return result
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout.splitlines()[-1])


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return path


def prepare(invoke, *, work, skill, pack, release_manifest, registry, version,
            relative="artifacts/deep/prototype.html", revision=False, previous=None):
    work.mkdir(parents=True, exist_ok=False)
    workspace = work / "workspace-with-arbitrary-name"
    workspace.mkdir()
    run_root, run_id = work / "builder-records", "SYN-SYNTHETIC"
    sources = {}
    definitions = [
        ("prd", "prd", "current-product-requirement", "text", "Synthetic entry\nAdd an entry.\nAt page top.\n"),
        ("runtime", "runtime", "observed-runtime", "text", "Observed the synthetic entry.\n"),
        ("code", "code", "current-code", "source-code", '<button id="entry">Entry</button>\n'),
        ("screenshot", "screenshot", "same-version-screenshot", "image", "synthetic-image-bytes"),
        ("baseline", "prototype-baseline", "current-prototype-baseline", "html", "<!doctype html><html><body>Before entry</body></html>"),
    ]
    if previous:
        definitions = [(n,k,a,c, previous["prototype"].read_text() if n=="baseline" else text + ("Show updated entry.\n" if n=="prd" else "")) for n,k,a,c,text in definitions]
    bundle = {"schemaVersion": "1.0.0", "sources": []}
    evidence_sources = []
    for name, kind, authority, content_kind, text in definitions:
        path = work / "evidence" / f"{name}.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        sources[authority] = path
        bundle["sources"].append({"sourceId": name, "sourceType": kind, "path": path.name,
            "origin": {"kind": "captured-runtime" if kind in {"runtime", "screenshot"} else "workspace-file", "locator": path.name}})
        evidence_sources.append({"sourceId": name, "sourceType": kind, "authority": authority,
            "label": name, "contentKind": content_kind, "sourceHash": digest(path)})
    decision = work / "decision.txt"
    decision.write_text("Synthetic product decision.\n")
    sources["confirmed-product-decision"] = decision
    prd_review = save(work / "prd-review.json", {"verdict": "pass"})
    assessment = save(work / "readiness.json", {"schema_version": 1,
        "prd_artifact_sha256": digest(sources["current-product-requirement"]),
        "review_artifact_sha256": digest(prd_review), "review_scope": "self_review",
        "review_evidence": "pass", "prototype_readiness": "pass", "spec_readiness": "not_assessed",
        "reason": "Synthetic technical fixture only.", "blocking_item_ids": [], "pending_interactions": []})
    entry = invoke("entry-create", "--run-root", run_root, "--run-id", run_id,
        "--prd-run-id", "SYN-PRD", "--review-context-id", "SYN-PRD-REVIEW",
        "--prd", sources["current-product-requirement"], "--review", prd_review,
        "--delivery", "standard", "--coverage", "pilot-slice", "--readiness-assessment", assessment,
        "--authorized-by", "synthetic-owner", "--authorized-at", NOW)["entry"]
    args = ["source-set-create", "--run-root", run_root, "--source-set-id", "SYN-SOURCES",
            "--entry", entry, "--selected-at", NOW]
    for role, path in sources.items():
        args.extend(["--source", f"{role}={path}"])
    source_set = invoke(*args)["source_set"]
    snapshot = invoke("start", "--run-root", run_root, "--run-id", run_id, "--entry", entry,
        "--source-set", source_set, "--manifest", release_manifest, "--registry", registry,
        "--skill-root", skill, "--pack-root", pack, "--source-registry", pack / "surfaces/source-registry.yaml" if (pack / "surfaces/source-registry.yaml").exists() else pack / "surfaces/surface-guidance.yaml",
        "--source-registry-version", version, "--workspace-root-id", "syn-workspace", "--created-at", NOW)["snapshot"]
    prototype = workspace / relative
    prototype.parent.mkdir(parents=True, exist_ok=True)
    prototype.write_text('<!doctype html><html><body><main id="view" class="active"><button id="entry">Entry</button>' + '<p>Unchanged synthetic record.</p>' * 20 + '</main></body></html>')
    def ref(excerpt, source="prd"):
        return {"sourceId": source, "locator": f"text:{excerpt}", "excerpt": excerpt}
    manifest = {"schemaVersion": "4.0.0",
        "requirement": {"id": "SYN-REQ", "title": "Synthetic entry", "coverage": "pilot-slice", "primarySourceId": "prd"},
        "prototype": {"sourceLabel": "prototype.html", "sourcePath": relative, "sourceHash": digest(prototype),
            "baselineSourceId": "baseline", "baselineHash": digest(sources["current-prototype-baseline"]), "sourceRole": "current-workspace-prototype"},
        "delivery": {"mode": "standard", "readerRole": "derived-readonly", "riskTriggers": [],
            "lineage": {"mode": "initial", "previousManifestSha256": None, "previousSnapshotSha256": None, "baselineReceiptSha256": None,
                "preservedCardIds": [], "modifiedCardIds": [], "addedCardIds": ["SYN-C1"], "removedCardIds": [], "removalDecisions": []},
            "readerComposition": {"coreItems": CORE, "customItems": [], "removedItems": []},
            "pendingReconciliation": {"carried": [], "resolved": [], "superseded": [], "new": []}},
        "baselineEvidence": {"mode": "existing-system-increment", "runtimePage": {"availability": "available", "sourceIds": ["runtime"], "note": "Synthetic observation"},
            "code": {"availability": "available", "sourceIds": ["code"], "note": "Synthetic code"},
            "screenshots": {"availability": "available", "sourceIds": ["screenshot"], "note": "Synthetic screenshot"}, "conflicts": []},
        "scopeEvidence": {"productScope": {"summary": "Entry only", "included": ["Entry"], "excluded": [], "sourceRefs": [ref("Synthetic entry")]},
            "sampleSource": {"status": "present", "samples": [{"sampleId": "runtime", "label": "Synthetic", "context": "Entry observed", "sourceIds": ["runtime"]}], "note": "Synthetic"},
            "verifiedCoverage": {"verified": ["Entry"], "notVerified": [], "sourceRefs": [ref("Observed the synthetic entry.", "runtime")]}},
        "evidenceSources": evidence_sources, "pages": [{"pageId": "page", "title": "Page", "order": 1}],
        "surfaces": [{"surfaceId": "view:page", "kind": "view", "rootSelector": "#view", "activeSelector": "#view.active", "parentSurfaceIds": [], "exitActions": [], "exitGuardSurfaceIds": [], "snapshotSelectors": ["#view"]}],
        "globalRules": [], "cards": [{"cardId": "SYN-C1", "displayNumber": "1", "requirementId": "SYN-REQ", "pageId": "page", "surfaceId": "view:page", "region": "Top", "title": "Synthetic entry", "summary": "Add an entry.", "changeKinds": ["add"],
            "changeContent": {"added": ["Entry"], "placement": "At page top.", "finalResult": "Entry visible"},
            "fields": [], "rules": [], "interactions": [], "errors": [], "pendingItems": [], "history": [],
            "evidenceBindings": [{"claimPath": f"/cards/0/{field}", "sourceRefs": [ref(text)]} for field, text in [("title", "Synthetic entry"), ("summary", "Add an entry."), ("changeContent", "At page top.")]],
            "anchor": {"kind": "element", "selector": "#entry", "enterActions": [], "manualPath": "Page → Entry"}}]}
    manifest_path = save(work / "manifest.json", manifest)
    evidence = save(work / "evidence/evidence-bundle.json", bundle)
    previous_args = []
    if revision:
        assert previous is not None, "revision must consume actual initial Reader"
        previous_manifest = previous["manifest"]
        previous_reader, previous_meta = previous["reader"], previous["metadata"]
        baseline_receipt = work / "lineage-baseline-receipt.json"
        args = ["node", skill / "prototype-design-workflow/scripts/create-lineage-baseline-receipt.mjs",
             "--manifest", previous_manifest, "--snapshot", previous_reader, "--metadata", previous_meta,
             "--out", baseline_receipt, "--receipt-id", "SYN-BASELINE", "--requirement-id", "SYN-REQ",
             "--created-at", "2026-10-19T00:00:00Z"]
        result = subprocess.run(list(map(str,args)), env=getattr(invoke,"env",None), capture_output=True,text=True,timeout=180)
        save(work / "lineage-command.json", {"exit_code":result.returncode,"stdout":result.stdout,"stderr":result.stderr})
        assert result.returncode == 0, result.stdout + result.stderr
        old_card=json.loads(previous_manifest.read_text())["cards"][0]
        prototype.write_text(prototype.read_text().replace(">Entry</button>",">Updated entry</button>"))
        manifest["prototype"]["sourceHash"]=digest(prototype)
        card=manifest["cards"][0]
        card["summary"]="Show updated entry."
        card["changeContent"]["finalResult"]="Updated entry visible"
        card["history"]=[{"previousCardSha256":hashlib.sha256(json.dumps(old_card,ensure_ascii=False,separators=(",",":")).encode()).hexdigest(),"reason":"Show updated entry.","sourceRefs":[ref("Show updated entry.")]}]
        card["evidenceBindings"][1]["sourceRefs"]=[ref("Show updated entry.")]
        manifest["delivery"]["lineage"] = {"mode": "revision", "previousManifestSha256": digest(previous_manifest),
            "previousSnapshotSha256": digest(previous_reader), "baselineReceiptSha256": digest(baseline_receipt),
            "preservedCardIds": [], "modifiedCardIds": ["SYN-C1"], "addedCardIds": [], "removedCardIds": [], "removalDecisions": []}
        save(manifest_path, manifest)
        previous_args = ["--previous-manifest", previous_manifest, "--previous-snapshot", previous_reader, "--lineage-baseline-receipt", baseline_receipt]
    reader, metadata = work / "reader.html", work / "reader.html.build.json"
    validation_args = ["validate-output", "--run-root", run_root, "--snapshot", snapshot,
        "--prototype", prototype, "--validator-mode", "standard", "--skill-root", skill,
        "--manifest", manifest_path, "--evidence", evidence, "--workspace-root", workspace,
        "--evidence-root", evidence.parent, "--reader-snapshot", reader, "--reader-metadata", metadata, "--checked-at", NOW, *previous_args]
    receipt = invoke(*validation_args)["receipt"]
    builder = invoke("builder-handoff", "--run-root", run_root, "--builder-handoff-id", "SYN-BUILDER",
        "--builder-context-id", "SYN-BUILDER-CONTEXT", "--entry", entry, "--source-set", source_set,
        "--snapshot", snapshot, "--receipt", receipt, "--baseline-evidence", "pass",
        "--high-fidelity-readiness", "pass", "--prototype-sync", "pass", "--created-at", NOW)["builder_handoff"]
    report = save(work / "independent-report.json", {"schemaVersion": "2.0.0", "reviewId": "SYN-INDEPENDENT", "reviewerRole": "independent", "reviewedAt": "2026-10-19T00:00:00Z",
        "subject": {"manifestHash": digest(manifest_path), "prototypeHash": digest(prototype), "evidenceBundleHash": digest(evidence), "currentSnapshotHash": digest(reader), "previousManifestHash": digest(previous_manifest) if revision else None, "previousSnapshotHash": digest(previous_reader) if revision else None},
        "scopeReview": {"result": "pass", "evidenceRefs": ["evidence:prd"], "note": "Synthetic scope checked"},
        "pendingReview": {"result": "pass", "evidenceRefs": ["evidence:prd"], "note": "No pending items"},
        "negativeChecks": [{"checkId": name, "applicability": "not-applicable", "result": "not-applicable", "evidenceRefs": [], "note": "Synthetic static entry has no business mutation."} for name in CHECKS],
        "triggers": ["unverified-authoritative-source", "unverified-prototype-lineage"], "verdict": "pass", "findings": []})
    return {"work": work, "workspace": workspace, "builder": Path(builder), "report": report,
        "receipt": Path(receipt), "manifest": manifest_path, "evidence": evidence, "prototype": prototype,
        "reader": reader, "metadata": metadata, "snapshot": Path(snapshot), "validation_args": validation_args}


def close(invoke, case, *, output=None, extra=()):
    output = output or case["work"] / "separate-review-output"
    review = invoke("review-check", "--run-root", output, "--builder-handoff", case["builder"],
        "--builder-context-id", "SYN-BUILDER-CONTEXT", "--review-context-id", "SYN-INDEPENDENT-CONTEXT",
        "--independent-review", case["report"], "--reviewed-at", NOW, *extra)["prototype_review"]
    final = invoke("gate", "--run-root", output, "--builder-handoff", case["builder"],
        "--review", review, "--created-at", NOW, *extra)["prototype_handoff"]
    assert json.loads(Path(final).read_text())["gate_status"] == "pass"
    return Path(review), Path(final)


def negative_matrix(invoke, case, native_review):
    """Mutate only isolated test copies; each failure must write zero success files."""
    def rejected(name, args):
        output = case["work"] / f"rejected-{name}"
        result = invoke(*args, "--run-root", output, blocked=True)
        assert not list(output.rglob("*.json")), name
        return result
    review_args = ["review-check", "--builder-handoff", case["builder"],
        "--builder-context-id", "SYN-BUILDER-CONTEXT", "--review-context-id", "SYN-INDEPENDENT-CONTEXT",
        "--independent-review", case["report"], "--reviewed-at", NOW]
    bad_context = list(review_args)
    bad_context[bad_context.index("--review-context-id") + 1] = "SYN-BUILDER-CONTEXT"
    rejected("same-context", bad_context)
    gate_args = ["gate", "--builder-handoff", case["builder"], "--review", native_review, "--created-at", NOW]
    for field in ("manifest", "prototype", "evidence", "reader", "metadata", "report"):
        path, original = case[field], case[field].read_bytes()
        try:
            if field == "report":
                value = json.loads(original)
                value["subject"]["prototypeHash"] = "f" * 64
                save(path, value)
            else:
                path.write_bytes(original + b"\n ")
            rejected(f"review-drift-{field}", review_args)
            rejected(f"gate-drift-{field}", gate_args)
        finally:
            path.write_bytes(original)
    # Context is a bound receipt field, not a freely supplied Review override.
    original = json.loads(case["builder"].read_text())
    for name, alter in (
        ("root", lambda value: value["validation_context"].update(workspace_root=str(case["work"]))),
        ("roots", lambda value: value["validation_context"].update(evidence_roots=[str(case["work"])])),
    ):
        value = json.loads(json.dumps(original))
        alter(value)
        fake = save(case["work"] / f"wrong-{name}-builder.json", value)
        args = list(review_args)
        args[args.index("--builder-handoff") + 1] = fake
        rejected(f"wrong-{name}", args)
    # Wrong root and unsafe/mismatched identities are refused at Standard entry.
    for name, source in (("escape", "../prototype.html"), ("absolute", str(case["prototype"])), ("mismatch", "other.html")):
        value = json.loads(case["manifest"].read_text())
        value["prototype"]["sourcePath"] = source
        path = save(case["work"] / f"{name}-manifest.json", value)
        args = list(case["validation_args"])
        args[args.index("--manifest") + 1] = path
        args[args.index("--reader-snapshot") + 1] = case["work"] / f"{name}-reader.html"
        args[args.index("--reader-metadata") + 1] = case["work"] / f"{name}-metadata.json"
        rejected(name, args)
        assert not (case["work"] / f"{name}-reader.html").exists()
    args = list(case["validation_args"])
    args[args.index("--workspace-root") + 1] = case["prototype"].parent
    args[args.index("--reader-snapshot") + 1] = case["work"] / "wrong-root-reader.html"
    args[args.index("--reader-metadata") + 1] = case["work"] / "wrong-root-metadata.json"
    rejected("standard-wrong-root", args)
    return {"negative_cases": 19, "zero_success_writes": True}


def verify_workflow(backend,binding,output):
    import contextlib,io
    from plugin_runtime.manager import run_requirement
    home=backend.home
    root=Path(binding["runtime_root"]);skill=Path(binding["skill_root"]);pack=Path(binding["pack_root"])
    output.mkdir(parents=True,exist_ok=True)
    class BoundCLI:
        env=dict(os.environ,PLAYWRIGHT_BROWSERS_PATH=str(root/"browsers"))
        count=0
        def __call__(self,*args,blocked=False):
            self.count+=1
            with contextlib.redirect_stdout(io.StringIO()) as out,contextlib.redirect_stderr(io.StringIO()) as err:
                try:status=backend.invoke(binding,["prototype",*map(str,args)])
                except SystemExit as e:status=e.code
            save(output/"native-logs"/f"{self.count:03}.json",{"arguments":list(map(str,args)),"exit_code":status,"stdout":out.getvalue(),"stderr":err.getvalue()})
            if blocked:
                assert status!=0,out.getvalue()
                return type("Result",(),{"stdout":out.getvalue(),"stderr":err.getvalue()})()
            assert status in (0,None),out.getvalue()+err.getvalue()
            return json.loads(out.getvalue().splitlines()[-1])
    invoke=BoundCLI()
    with contextlib.ExitStack() as stack:
        from unittest.mock import patch
        stack.enter_context(patch.dict(os.environ,{"PLAYWRIGHT_BROWSERS_PATH":str(root/"browsers")}))
        first=prepare(invoke,work=output/"initial",skill=skill,pack=pack,release_manifest=root/"kit/registry/prototype-canary-manifest.yaml",registry=root/"kit/registry",version=binding["distribution_version"])
        first_review,first_gate=close(invoke,first)
        protected={p:digest(p) for p in first["work"].rglob("*") if p.is_file()}
        revision=prepare(invoke,work=output/"revision",skill=skill,pack=pack,release_manifest=root/"kit/registry/prototype-canary-manifest.yaml",registry=root/"kit/registry",version=binding["distribution_version"],revision=True,previous=first)
        review,gate=close(invoke,revision)
        negatives=negative_matrix(invoke,revision,review)
        assert all(digest(p)==value for p,value in protected.items())
        assert first["prototype"].read_bytes()!=revision["prototype"].read_bytes()
    result={"status":"pass","synthetic":True,"real_independent_business_review":False,"initial_gate":str(first_gate),"revision_gate":str(gate),"initial_files_protected":len(protected),"card_id_preserved":True,"actual_revision":True,**negatives}
    save(output/"RESULT.json",result);return result
