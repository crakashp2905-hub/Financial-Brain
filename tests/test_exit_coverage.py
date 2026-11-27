from decimal import Decimal

import pytest

from financial_brain.portfolio.exit_coverage import (
    ExitCoverageEvidence,
    ExitCoverageError,
    ExitProduct,
    OpenPositionForExitCoverage,
    audit_canonical_snapshot_exit_coverage,
    audit_exit_coverage,
)
from financial_brain.portfolio.canonical_ledger import (
    AssetClass,
    CanonicalPortfolioLedger,
    EventKind,
    PortfolioEvent,
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


def test_canonical_snapshot_adapter_audits_every_open_sleeve_and_ignores_flat_rows():
    snapshot = {"positions": [
        {"asset_class": "EQUITY", "instrument_id": "NSE:INFY", "sleeve_id": "swing",
         "quantity": Decimal("10")},
        {"asset_class": "MUTUAL_FUND", "instrument_id": "INF000000001", "sleeve_id": "long-term",
         "quantity": Decimal("12.5")},
        {"asset_class": "ETF", "instrument_id": "NSE:NIFTYBEES", "sleeve_id": "hedge",
         "quantity": Decimal("0")},
    ]}
    equity_id = "EQUITY:NSE:INFY:swing"
    mf_id = "MUTUAL_FUND:INF000000001:long-term"
    nav = _evidence(mf_id, exit_policy_sha256=None, exception_code="NAV_SETTLEMENT",
                    exception_reason="NAV redemption follows its settlement calendar",
                    exception_evidence_sha256=HASH)
    report = audit_canonical_snapshot_exit_coverage(
        snapshot=snapshot, evidence=(_evidence(equity_id), nav))
    assert report.complete
    assert report.open_position_count == report.covered_position_count == 2


def test_canonical_snapshot_adapter_treats_futures_as_exchange_traded_positions():
    snapshot = {"positions": [{"asset_class": "FUTURE", "instrument_id": "NFO:NIFTY-FUT",
                               "sleeve_id": "hedge", "quantity": Decimal("25")}]}
    report = audit_canonical_snapshot_exit_coverage(snapshot=snapshot, evidence=())

    assert report.open_position_count == 1
    assert report.gaps[0].position_id == "FUTURE:NFO:NIFTY-FUT:hedge"
    assert report.gaps[0].reason_codes == ("MISSING_EXIT_COVERAGE",)


def test_canonical_snapshot_adapter_fails_when_any_open_sleeve_lacks_monitor():
    snapshot = {"positions": [
        {"asset_class": "EQUITY", "instrument_id": "NSE:INFY", "sleeve_id": "swing",
         "quantity": "10"},
        {"asset_class": "EQUITY", "instrument_id": "NSE:INFY", "sleeve_id": "intraday",
         "quantity": "1"},
    ]}
    report = audit_canonical_snapshot_exit_coverage(snapshot=snapshot, evidence=(_evidence(
        "EQUITY:NSE:INFY:swing"),))
    assert not report.complete
    assert report.open_position_count == 2
    assert report.gaps[0].position_id == "EQUITY:NSE:INFY:intraday"
    assert report.gaps[0].reason_codes == ("MISSING_EXIT_COVERAGE",)


def test_exit_coverage_can_consume_real_canonical_ledger_snapshot(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "portfolio.sqlite")
    ledger.append(PortfolioEvent(
        account_id="paper-main", sleeve_id="swing", source_system="fixture",
        source_event_id="buy-1", source_sha256=HASH, event_kind=EventKind.FILL,
        occurred_at="2026-11-20T10:00:00+05:30", asset_class=AssetClass.EQUITY,
        instrument_id="NSE:INFY", side="BUY", quantity="10", price="100",
    ))
    snapshot = ledger.snapshot("paper-main")
    report = audit_canonical_snapshot_exit_coverage(snapshot=snapshot, evidence=())
    assert report.open_position_count == 1
    assert report.gaps[0].position_id == "EQUITY:NSE:INFY:swing"
    assert report.gaps[0].reason_codes == ("MISSING_EXIT_COVERAGE",)
