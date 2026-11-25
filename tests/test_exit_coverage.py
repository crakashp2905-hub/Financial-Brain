from decimal import Decimal

import pytest

from financial_brain.portfolio.exit_coverage import (
    ExitCoverageEvidence,
    ExitCoverageError,
    ExitProduct,
    OpenPositionForExitCoverage,
    audit_exit_coverage,
)


HASH = "a" * 64


def _exchange(position_id="equity-1"):
    return OpenPositionForExitCoverage(position_id, "NSE:INFY", ExitProduct.EXCHANGE_TRADED,
                                       Decimal("10"))


def _evidence(position_id="equity-1", **changes):
    values = dict(position_id=position_id, monitor_id="monitor-1", last_processed_event_id="evt-9",
                  replayed_through_sequence=9, journal_hash_chain_valid=True,
                  exit_policy_sha256=HASH)
    return ExitCoverageEvidence(**(values | changes))


def test_exchange_position_requires_policy_and_replay_verified_monitor():
    report = audit_exit_coverage(positions=(_exchange(),), evidence=(_evidence(),))
    assert report.complete
    assert report.open_position_count == report.covered_position_count == 1


def test_nav_mutual_fund_requires_auditable_exception_and_still_needs_monitor():
    position = OpenPositionForExitCoverage("mf-1", "INF000000001", ExitProduct.NAV_MUTUAL_FUND,
                                           Decimal("12.5"))
    exception = _evidence("mf-1", exit_policy_sha256=None, exception_code="NAV_SETTLEMENT",
                          exception_reason="Redemption uses applicable NAV and settlement calendar",
                          exception_evidence_sha256=HASH)
    assert audit_exit_coverage(positions=(position,), evidence=(exception,)).complete
    no_monitor = _evidence("mf-1", monitor_id="", exit_policy_sha256=None,
                           exception_code="NAV_SETTLEMENT", exception_reason="NAV settlement",
                           exception_evidence_sha256=HASH)
    report = audit_exit_coverage(positions=(position,), evidence=(no_monitor,))
    assert not report.complete
    assert "MISSING_CRASH_RESUMABLE_MONITOR" in report.gaps[0].reason_codes


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"exit_policy_sha256": None}, "MISSING_OR_INVALID_EXIT_POLICY"),
        ({"journal_hash_chain_valid": False}, "MONITOR_JOURNAL_UNVERIFIED"),
        ({"replayed_through_sequence": 0}, "MISSING_CRASH_RESUMABLE_MONITOR"),
    ],
)
def test_exchange_position_gaps_fail_closed(changes, expected):
    report = audit_exit_coverage(positions=(_exchange(),), evidence=(_evidence(**changes),))
    assert not report.complete
    assert expected in report.gaps[0].reason_codes


def test_missing_and_orphan_coverage_rows_are_reported():
    report = audit_exit_coverage(positions=(_exchange(),), evidence=(_evidence("closed-position"),))
    assert {gap.reason_codes for gap in report.gaps} == {
        ("MISSING_EXIT_COVERAGE",), ("ORPHAN_EXIT_COVERAGE_EVIDENCE",)
    }


def test_duplicate_position_ids_are_rejected():
    with pytest.raises(ExitCoverageError, match="duplicate open position"):
        audit_exit_coverage(positions=(_exchange(), _exchange()), evidence=())
