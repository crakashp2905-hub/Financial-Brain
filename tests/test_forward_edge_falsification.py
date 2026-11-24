import pytest

from financial_brain.forward_edge.falsification import (
    REQUIRED_FALSIFICATIONS,
    FalsificationError,
    FalsificationEvidence,
    build_falsification_report,
)


def _all(*, failed=None):
    return tuple(FalsificationEvidence(name, f"hash-{name}", True, name != failed,
                                        1 if name == failed else 0, f"{name} fixture evidence")
                 for name in REQUIRED_FALSIFICATIONS)


def test_missing_falsification_evidence_downgrades_to_insufficient_not_pass() -> None:
    evidence = tuple(item for item in _all() if item.check_name != "delisting")
    report = build_falsification_report(evidence)
    assert report.overall_status == "INSUFFICIENT"
    assert report.missing_checks == ("delisting",)
    assert next(row for row in report.check_results if row["check_name"] == "delisting")["status"] == "INSUFFICIENT"
    assert not report.promotion_allowed


def test_failed_check_and_all_pass_have_distinct_statuses() -> None:
    failed = build_falsification_report(_all(failed="factor_overlap"))
    assert failed.overall_status == "FAIL"
    passed = build_falsification_report(_all())
    assert passed.overall_status == "PASS"
    assert not passed.promotion_allowed


def test_unexecuted_check_and_duplicate_check_are_not_silently_accepted() -> None:
    not_run = FalsificationEvidence("missed_fills", "hash", False, False, 0, "not run")
    report = build_falsification_report((not_run,))
    assert report.overall_status == "INSUFFICIENT"
    assert "missed_fills" in report.missing_checks
    with pytest.raises(FalsificationError, match="only once"):
        build_falsification_report((not_run, not_run))
