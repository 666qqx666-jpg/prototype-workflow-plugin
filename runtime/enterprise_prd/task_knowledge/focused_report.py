from __future__ import annotations

import importlib
import unittest


EXPECTED_TKMI_CLAIMS = tuple(f"TKMI-{number:02d}" for number in range(1, 13))
FOCUSED_MODULES = (
    "tests.test_task_knowledge_contracts", "tests.test_task_knowledge_store", "tests.test_task_knowledge_builder",
    "tests.test_task_knowledge_bridge_outline", "tests.test_task_knowledge_bridge_sections", "tests.test_task_knowledge_role_section_index",
    "tests.test_task_knowledge_writer_input", "tests.test_task_knowledge_fixture_flow", "tests.test_task_knowledge_cli",
)


class _Result(unittest.TestResult):
    def __init__(self):
        super().__init__(); self.executed: list[tuple[str, tuple[str, ...]]] = []
    def addSuccess(self, test):
        super().addSuccess(test)
        method = getattr(test, test._testMethodName)
        claims = getattr(method, "covered_claim_ids", None)
        if type(claims) is tuple and claims:
            self.executed.append((test.id(), claims))
        else:
            self.addFailure(test, (AssertionError, AssertionError("focused test has no claim metadata"), None))


def run_focused_suite_and_collect_report() -> dict:
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(name)) for name in FOCUSED_MODULES)
    result = _Result(); suite.run(result)
    names = [name for name, _ in result.executed]
    coverage = {claim: [] for claim in EXPECTED_TKMI_CLAIMS}
    for name, claims in result.executed:
        for claim in claims:
            if claim in coverage: coverage[claim].append(name)
    return {"executed_test_cases": names, "covered_claim_ids": sorted({claim for _, claims in result.executed for claim in claims}), "claim_coverage": coverage, "focused_tests_run": len(names), "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped)}
