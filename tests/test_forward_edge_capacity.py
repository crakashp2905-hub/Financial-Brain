import pytest
from datetime import date, datetime, timezone

from financial_brain.forward_edge.capacity import (
    CapacityCostPoint,
    CapacityError,
    CapacityUniverseInput,
    evaluate_capacity,
    report_capacity_universe,
)


def _curve():
    return (CapacityCostPoint(0.005, 5, 40), CapacityCostPoint(0.01, 10, 45),
            CapacityCostPoint(0.02, 30, 50))


def _report(*, attested=True, coverage=0.98):
    return evaluate_capacity(gross_alpha_bps=20, adv_notional=1_000_000,
                             visible_depth_notional=15_000,
                             notionals=(5_000, 10_000, 20_000, 30_000),
                             cost_curve=_curve(), source_hash="calibration-sha",
                             owner_attested_live_observations=attested,
                             minimum_trades_per_point=30, observed_quote_coverage=coverage)


def test_capacity_curve_degrades_net_alpha_and_caps_at_supported_depth() -> None:
    report = _report()
    assert [item.expected_net_alpha_bps for item in report.scenarios] == [15, 10, -10, None]
    assert report.recommended_max_notional == 10_000
    assert report.scenarios[2].within_visible_depth is False
    assert "CONDITIONAL" in report.recommendation_status


def test_fixture_or_low_coverage_never_produces_size_recommendation() -> None:
    assert _report(attested=False).recommended_max_notional is None
    low = _report(coverage=0.80)
    assert low.recommended_max_notional is None
    assert not any(row.supported_by_calibration for row in low.scenarios)


def test_cost_curve_must_be_monotone_and_no_extrapolation_is_allowed() -> None:
    with pytest.raises(CapacityError, match="non-decreasing"):
        evaluate_capacity(gross_alpha_bps=20, adv_notional=1_000_000,
                          visible_depth_notional=100_000, notionals=(10_000,),
                          cost_curve=(CapacityCostPoint(0.01, 20, 50),
                                      CapacityCostPoint(0.02, 10, 50)),
                          source_hash="x", owner_attested_live_observations=True,
                          observed_quote_coverage=1.0)
    row = _report().scenarios[-1]
    assert row.cost_bps is None
    assert row.expected_net_alpha_bps is None


def test_universe_capacity_report_hashes_dated_adv_depth_and_impact_rows() -> None:
    as_of = datetime(2026, 11, 16, 4, 0, tzinfo=timezone.utc)
    fresh = CapacityUniverseInput(
        "NSE:ABC", 20, 1_000_000, 15_000, (5_000, 10_000), _curve(),
        "snapshot-hash", date(2026, 11, 13),
        datetime(2026, 11, 16, 3, 59, 59, tzinfo=timezone.utc), 0.98, True)
    stale_depth = CapacityUniverseInput(
        "NSE:OLD", 20, 1_000_000, 15_000, (5_000, 10_000), _curve(),
        "old-hash", date(2026, 11, 13),
        datetime(2026, 11, 16, 3, 0, tzinfo=timezone.utc), 0.98, True)
    report = report_capacity_universe(as_of=as_of, session=date(2026, 11, 16),
                                      instruments=(stale_depth, fresh))
    assert report.rows[0].instrument_id == "NSE:ABC"
    assert report.rows[0].report is not None
    assert report.rows[0].report.recommended_max_notional == 10_000
    assert report.rows[1].report is None
    assert report.rows[1].status == "NO_RECOMMENDATION_STALE_DEPTH"
    assert len(report.report_hash) == 64


def test_universe_capacity_report_rejects_future_and_duplicate_inputs() -> None:
    as_of = datetime(2026, 11, 16, 4, 0, tzinfo=timezone.utc)
    later = CapacityUniverseInput(
        "NSE:ABC", 20, 1_000_000, 15_000, (5_000, 10_000), _curve(),
        "snapshot-hash", date(2026, 11, 17),
        datetime(2026, 11, 16, 3, 59, tzinfo=timezone.utc), 0.98)
    with pytest.raises(CapacityError, match="future"):
        report_capacity_universe(as_of=as_of, session=date(2026, 11, 16), instruments=(later,))
    with pytest.raises(CapacityError, match="unique"):
        report_capacity_universe(as_of=as_of, session=date(2026, 11, 16),
                                 instruments=(later, later))
