"""Evidence-complete falsification report for common trading research failures (FE-0103)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


REQUIRED_FALSIFICATIONS = (
    "look_ahead", "survivorship", "delisting", "factor_overlap", "rejected_trades", "missed_fills",
)


class FalsificationError(ValueError):
    """Invalid or ambiguous falsification evidence."""


@dataclass(frozen=True)
class FalsificationEvidence:
    check_name: str
    evidence_hash: str
    executed: bool
    passed: bool
    issue_count: int
    note: str

    def __post_init__(self) -> None:
        if self.check_name not in REQUIRED_FALSIFICATIONS:
            raise FalsificationError("unknown falsification check")
        if not self.evidence_hash.strip() or not self.note.strip():
            raise FalsificationError("falsification evidence hash and note are required")
        if type(self.issue_count) is not int or self.issue_count < 0:
            raise FalsificationError("issue_count must be a non-negative integer")
        if not self.executed and (self.passed or self.issue_count):
            raise FalsificationError("unexecuted checks cannot claim pass/fail issues")


@dataclass(frozen=True)
class FalsificationReport:
    check_results: tuple[dict[str, object], ...]
    overall_status: str
    missing_checks: tuple[str, ...]
    promotion_allowed: bool
    report_hash: str


def build_falsification_report(evidence: tuple[FalsificationEvidence, ...]) -> FalsificationReport:
    """Aggregate required checks, treating absent/unrun evidence as INSUFFICIENT."""
    names = [item.check_name for item in evidence]
    if len(names) != len(set(names)):
        raise FalsificationError("each falsification check may appear only once")
    by_name = {item.check_name: item for item in evidence}
    missing = tuple(name for name in REQUIRED_FALSIFICATIONS
                    if name not in by_name or not by_name[name].executed)
    results: list[dict[str, object]] = []
    for name in REQUIRED_FALSIFICATIONS:
        item = by_name.get(name)
        if item is None or not item.executed:
            status = "INSUFFICIENT"
        else:
            status = "PASS" if item.passed and item.issue_count == 0 else "FAIL"
        results.append({"check_name": name, "status": status,
                        "evidence_hash": item.evidence_hash if item else None,
                        "issue_count": item.issue_count if item else None,
                        "note": item.note if item else "required evidence not supplied"})
    statuses = {str(item["status"]) for item in results}
    if "INSUFFICIENT" in statuses:
        overall = "INSUFFICIENT"
    elif "FAIL" in statuses:
        overall = "FAIL"
    else:
        overall = "PASS"
    body = {"check_results": results, "overall_status": overall,
            "missing_checks": list(missing), "promotion_allowed": False}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return FalsificationReport(tuple(results), overall, missing, False, digest)
