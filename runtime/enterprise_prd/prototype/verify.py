from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from enterprise_prd.manifest import manifest_from_file

from .errors import ContractError, GateBlockedError, IntegrityError
from .store import sha256_file, write_once_json
from .version import COMPONENT_VERSION


EXPECTED_TAGS = tuple(f"EPTC-{index:02d}" for index in range(1, 15))
EXPECTED_FIXTURES = {
    "current-product-requirement.md",
    "review-report.json",
    "confirmed-product-decision.md",
    "observed-runtime.html",
    "current-code.tsx",
    "same-version-screenshot.svg",
    "current-prototype-baseline.html",
    "prototype-output.html",
    "findings-pass.json",
    "previous-manifest-v3.json",
    "previous-reader-snapshot.html",
    "lineage-baseline-receipt.json",
    "independent-review-v2-pass.json",
    "independent-review-v2-block.json",
    "product-convergence-record.json",
}
TAG_TESTS = {
    "EPTC-01": (
        "tests.test_prototype_skill_source.PrototypeSkillSourceTest.test_eptc_01_core_source_is_clean_and_complete",
        "tests.test_prototype_release.PrototypeReleaseTests.test_skill_archive_has_no_dependencies_or_personal_content",
    ),
    "EPTC-02": (
        "tests.test_prototype_skill_source.PrototypeSkillSourceTest.test_eptc_02_host_protocol_is_routed_not_inlined",
    ),
    "EPTC-03": (
        "tests.test_prototype_skill_pack.PrototypeSkillPackTest.test_eptc_03_enterprise_entrypoints_are_complete",
    ),
    "EPTC-04": (
        "tests.test_prototype_skill_pack.PrototypeSkillPackTest.test_eptc_04_company_pack_is_loadable_and_generic",
    ),
    "EPTC-05": ("tests.test_prototype_contracts",),
    "EPTC-06": (
        "tests.test_prototype_readiness",
        "tests.test_prototype_contracts.PrototypeContractTests.test_entry_matches_approved_spec_and_requires_two_pass_states",
        "tests.test_prototype_start.PrototypeStartTests.test_create_entry_blocks_failed_review_readiness_or_authorizer",
    ),
    "EPTC-07": (
        "tests.test_prototype_local_sources",
        "tests.test_prototype_start.PrototypeStartTests.test_source_set_requires_exact_six_roles",
        "tests.test_prototype_start.PrototypeStartTests.test_source_set_binds_current_prd_to_entry",
    ),
    "EPTC-08": (
        "tests.test_prototype_start.PrototypeStartTests.test_start_locks_all_candidate_inputs",
        "tests.test_prototype_start.PrototypeStartTests.test_start_rejects_installed_skill_or_pack_drift",
        "tests.test_prototype_start.PrototypeStartTests.test_start_allows_only_declared_generated_skill_dependencies",
        "tests.test_prototype_start.PrototypeStartTests.test_start_rejects_other_unpublished_skill_content",
        "tests.test_prototype_cli.PrototypeCliTests.test_gate_manifest_drift_returns_restart_required",
    ),
    "EPTC-09": (
        "tests.test_prototype_store.PrototypeStoreTests.test_write_once_is_idempotent_for_identical_bytes",
        "tests.test_prototype_store.PrototypeStoreTests.test_two_processes_cannot_publish_different_payloads",
    ),
    "EPTC-10": (
        "tests.test_prototype_store.PrototypeStoreTests.test_safe_run_path_rejects_parent_traversal",
        "tests.test_prototype_release.PrototypeReleaseTests.test_update_rejects_archive_path_traversal",
    ),
    "EPTC-11": (
        "tests.test_prototype_standard_native",
        "tests.test_prototype_validation.PrototypeValidationTests.test_standard_v3_returns_restart_required",
        "tests.test_prototype_handoff",
        "tests.test_prototype_review",
        "tests.test_prototype_validation.PrototypeValidationTests.test_checked_in_fixture_drives_the_light_run",
        "tests.test_prototype_validation.PrototypeValidationTests.test_light_rejects_skill_tree_drift_from_snapshot",
        "tests.test_prototype_handoff.PrototypeHandoffTests.test_builder_rejects_release_archive_drift",
        "tests.test_prototype_handoff.PrototypeHandoffTests.test_builder_rejects_installed_skill_or_pack_drift",
        "tests.test_prototype_review.PrototypeReviewTests.test_gate_rejects_release_archive_and_installed_skill_drift",
        "tests.test_prototype_review.PrototypeReviewTests.test_gate_rejects_installed_pack_contract_drift",
        "tests.test_prototype_cli.PrototypeCliTests.test_validate_skill_drift_returns_restart_required",
        "tests.test_prototype_cli.PrototypeCliTests.test_builder_archive_drift_returns_restart_required",
        "tests.test_prototype_cli.PrototypeCliTests.test_start_validate_builder_handoff_review_gate_light_flow",
    ),
    "EPTC-12": (
        "tests.test_prototype_validation.PrototypeValidationTests.test_standard_v3_returns_restart_required",
        "tests.test_prototype_validation.PrototypeValidationTests.test_standard_v3_does_not_write_receipt_or_run_validator",
        "tests.test_prototype_validation.PrototypeValidationTests.test_standard_rejects_fake_validator_not_locked_by_snapshot",
        "tests.test_prototype_contracts.PrototypeContractTests.test_v4_receipt_closes_review_and_lineage_bindings",
    ),
    "EPTC-13": (
        "tests.test_install.InstallTest.test_explicit_prototype_skill_set_links_five_skills",
        "tests.test_install.InstallTest.test_dependency_restore_runs_all_discovered_tests",
        "tests.test_install.InstallTest.test_failed_browser_restore_writes_no_receipt",
        "tests.test_prototype_code_query_skill",
        "distribution.tests.test_prototype_kit",
    ),
    "EPTC-14": (
        "tests.test_prototype_release",
        "tests.test_install.InstallTest.test_install_receipt_and_rollback_restore_previous_state",
        "tests.test_install.InstallTest.test_partial_link_failure_restores_all_previous_targets",
        "tests.test_install.InstallTest.test_default_skill_set_remains_three_prd_skills",
    ),
}
EVIDENCE_KEYS = {
    "schema_version", "component", "version", "status", "candidate_commit",
    "spec_sha256", "plan_sha256", "manifest_sha256",
    "skill_archive_sha256", "pack_archive_sha256", "tags", "commands",
    "baseline_tests", "prototype_tests", "generated_at",
}
BASELINE_SCRIPT = """
import pathlib
import unittest
excluded = {
    "test_knowledge_onboarding_genesis_security",
    "test_knowledge_onboarding_genesis_verify",
    "test_knowledge_onboarding_verify",
    "test_knowledge_onboarding_genesis_0_4_0_verify",
}
loader = unittest.defaultTestLoader
suite = unittest.TestSuite()
for path in sorted(pathlib.Path("tests").glob("test_*.py")):
    if path.stem not in excluded:
        suite.addTests(loader.discover("tests", pattern=path.name, top_level_dir="tests"))
result = unittest.TextTestRunner(verbosity=1).run(suite)
print(f"baseline_aware_tests={result.testsRun}")
raise SystemExit(0 if result.wasSuccessful() and result.testsRun >= 396 else 1)
""".strip()


def _run(command: list[str], repository: Path, label: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        command,
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise GateBlockedError(f"{label} failed")
    return result


def _test_tag(tag: str, repository: Path) -> dict:
    selectors = TAG_TESTS[tag]
    _run([sys.executable, "-m", "unittest", *selectors, "-v"], repository, tag)
    return {
        "tag": tag,
        "status": "pass",
        "checks": [{"name": selector, "passed": True} for selector in selectors],
    }


def _fixture_preflight(repository: Path, fixture_root: Path) -> None:
    repository = repository.resolve()
    fixture_root = fixture_root.resolve()
    try:
        fixture_root.relative_to(repository)
    except ValueError as exc:
        raise ContractError("fixture root must be inside repository") from exc
    files = {path.name for path in fixture_root.iterdir() if path.is_file()}
    if files != EXPECTED_FIXTURES:
        raise ContractError("fixture file set is invalid")
    payload = b"\n".join(
        path.read_bytes() for path in sorted(fixture_root.iterdir()) if path.is_file()
    )
    private_tokens = (
        b"/Users/",
        b"Obsidian" + b" Vault",
        b"BEGIN PRIVATE" + b" KEY",
    )
    for token in private_tokens:
        if token in payload:
            raise GateBlockedError("fixture contains private content")


def collect_fixture_evidence(
    *,
    repository: Path,
    fixture_root: Path,
    candidate_ref: str,
    spec_path: Path,
    plan_path: Path,
    manifest_path: Path,
) -> dict:
    repository = repository.resolve()
    head = _run(["git", "rev-parse", "HEAD"], repository, "candidate identity").stdout.strip()
    if head != candidate_ref:
        raise IntegrityError("candidate ref differs from repository HEAD")
    _fixture_preflight(repository, fixture_root)
    manifest_path = manifest_path.resolve()
    manifest = manifest_from_file(manifest_path)
    if (
        manifest.channel != "canary"
        or manifest.skill.name != "enterprise-prototype-skills"
        or manifest.company_pack.name != "company-prototype"
        or manifest.skill.version != COMPONENT_VERSION
        or manifest.company_pack.version != COMPONENT_VERSION
    ):
        raise ContractError("Prototype Canary Manifest identity is invalid")
    skill_archive = (manifest_path.parent / manifest.skill.archive).resolve()
    pack_archive = (manifest_path.parent / manifest.company_pack.archive).resolve()
    if (
        sha256_file(skill_archive) != manifest.skill.sha256
        or sha256_file(pack_archive) != manifest.company_pack.sha256
    ):
        raise IntegrityError("Prototype release archive hash changed")

    tags = [_test_tag(tag, repository) for tag in EXPECTED_TAGS]
    baseline_result = _run(
        [sys.executable, "-c", BASELINE_SCRIPT],
        repository,
        "baseline-aware regression",
    )
    baseline_output = baseline_result.stdout + "\n" + baseline_result.stderr
    baseline_match = re.search(r"baseline_aware_tests=(\d+)", baseline_output)
    if baseline_match is None:
        raise GateBlockedError("baseline test count is missing")
    baseline_tests = int(baseline_match.group(1))
    if baseline_tests < 396:
        raise GateBlockedError("baseline regression is below 396 tests")
    prototype_tests = sorted(str(path.relative_to(repository)) for path in
        (repository / "prototype-skills/prototype-design-workflow/tests").glob("*.test.mjs"))
    prototype_result = _run(
        ["node", "--test", *prototype_tests],
        repository,
        "Prototype core regression",
    )
    prototype_output = prototype_result.stdout + "\n" + prototype_result.stderr
    tests_match = re.search(r"(?m)^(?:#|ℹ) tests (\d+)$", prototype_output)
    pass_match = re.search(r"(?m)^(?:#|ℹ) pass (\d+)$", prototype_output)
    if (
        tests_match is None
        or pass_match is None
        or int(tests_match.group(1)) < 32
        or int(pass_match.group(1)) != int(tests_match.group(1))
        or re.search(r"(?m)^(?:#|ℹ) fail ([1-9]\d*)$", prototype_output)
    ):
        raise GateBlockedError("Prototype core regression must pass all discovered tests (at least 32)")
    commands = [
        {"name": tag, "status": "pass", "selectors": len(TAG_TESTS[tag])}
        for tag in EXPECTED_TAGS
    ]
    commands.extend((
        {"name": "baseline-aware-regression", "status": "pass", "tests": baseline_tests},
        {"name": "prototype-core-regression", "status": "pass", "tests": int(tests_match.group(1))},
    ))
    query_result = _run(["node", "--test", "prototype-skills/enterprise-prototype-code-query/tests/code-query.test.cjs"],
                        repository, "code query security regression")
    query_output = query_result.stdout + "\n" + query_result.stderr
    query_counts = [re.search(r"(?m)^(?:#|ℹ) " + label + r" (\d+)$", query_output) for label in ("tests", "pass", "fail")]
    if any(match is None for match in query_counts) or int(query_counts[0][1]) < 19 or \
            int(query_counts[0][1]) != int(query_counts[1][1]) or int(query_counts[2][1]) != 0:
        raise GateBlockedError("code query security regression is not fully passing")
    wrapper = _run([sys.executable, "-m", "unittest", "distribution.tests.test_prototype_kit", "-v"],
                   repository, "pilot kit regression")
    wrapper_count = re.search(r"Ran (\d+) tests", wrapper.stdout + "\n" + wrapper.stderr)
    if wrapper_count is None or int(wrapper_count[1]) < 47:
        raise GateBlockedError("pilot kit regression count is missing")
    commands.extend(({"name": "code-query-security-regression", "status": "pass", "tests": int(query_counts[0][1])},
                     {"name": "pilot-kit-regression", "status": "pass", "tests": int(wrapper_count[1])}))
    return {
        "schema_version": 1,
        "component": "enterprise-prototype-team-candidate",
        "version": COMPONENT_VERSION,
        "status": "fixture_pass",
        "candidate_commit": candidate_ref,
        "spec_sha256": sha256_file(spec_path),
        "plan_sha256": sha256_file(plan_path),
        "manifest_sha256": sha256_file(manifest_path),
        "skill_archive_sha256": manifest.skill.sha256,
        "pack_archive_sha256": manifest.company_pack.sha256,
        "tags": tags,
        "commands": commands,
        "baseline_tests": baseline_tests,
        "prototype_tests": int(tests_match.group(1)),
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }


def write_fixture_evidence(path: Path, document: dict) -> str:
    if type(document) is not dict or set(document) != EVIDENCE_KEYS:
        raise ContractError("Fixture Evidence fields are invalid")
    if (
        document["status"] != "fixture_pass"
        or [item["tag"] for item in document["tags"]] != list(EXPECTED_TAGS)
        or any(item["status"] != "pass" for item in document["tags"])
    ):
        raise GateBlockedError("Fixture Evidence is not fully passing")
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True)
    if "/Users/" in payload or ("Obsidian" + " Vault") in payload:
        raise GateBlockedError("Fixture Evidence contains private paths")
    return write_once_json(path, document)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="enterprise-prd-prototype-verify")
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--candidate-ref", required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    document = collect_fixture_evidence(
        repository=args.repository,
        fixture_root=args.fixture_root,
        candidate_ref=args.candidate_ref,
        spec_path=args.spec,
        plan_path=args.plan,
        manifest_path=args.manifest,
    )
    digest = write_fixture_evidence(args.output, document)
    print(json.dumps({"status": "pass", "output": str(args.output), "sha256": digest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
