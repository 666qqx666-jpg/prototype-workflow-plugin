from enterprise_prd.contracts import (
    GateDecision,
    ReviewEvidence,
    ReviewIssue,
    ReviewReport,
    ReviewSeverity,
    SpecReadiness,
)
from enterprise_prd.errors import ContractError


REPORT_KEYS = {
    "run_id", "review_context_id", "review_evidence",
    "spec_readiness", "issues", "reviewed_artifact_sha256",
}
ISSUE_KEYS = {
    "issue_id", "severity", "title", "evidence_ref", "required_change",
}


def evaluate_gate(report: ReviewReport) -> GateDecision:
    report.validate()
    reasons: list[str] = []
    if report.review_evidence is ReviewEvidence.BLOCK:
        reasons.append("evidence_block")
    if report.spec_readiness is not SpecReadiness.PASS:
        reasons.append("spec_not_ready")
    if any(issue.severity is ReviewSeverity.BLOCKER for issue in report.issues):
        reasons.append("blocker_open")
    return GateDecision(passed=not reasons, reason_codes=tuple(reasons))


def report_from_dict(raw: dict) -> ReviewReport:
    if type(raw) is not dict or set(raw) != REPORT_KEYS or type(raw["issues"]) is not list:
        raise ContractError("review report fields are invalid")
    issues = []
    for item in raw["issues"]:
        if type(item) is not dict or set(item) != ISSUE_KEYS:
            raise ContractError("review issue fields are invalid")
        try:
            severity = ReviewSeverity(item["severity"])
        except ValueError as exc:
            raise ContractError("review issue severity is invalid") from exc
        issues.append(ReviewIssue(
            issue_id=item["issue_id"],
            severity=severity,
            title=item["title"],
            evidence_ref=item["evidence_ref"],
            required_change=item["required_change"],
        ))
    try:
        review_evidence = ReviewEvidence(raw["review_evidence"])
        spec_readiness = SpecReadiness(raw["spec_readiness"])
    except ValueError as exc:
        raise ContractError("review verdict is invalid") from exc
    report = ReviewReport(
        run_id=raw["run_id"],
        review_context_id=raw["review_context_id"],
        review_evidence=review_evidence,
        spec_readiness=spec_readiness,
        issues=tuple(issues),
        reviewed_artifact_sha256=raw["reviewed_artifact_sha256"],
    )
    report.validate()
    return report
