from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.paired_paper import (
    EligibleSignal,
    PairedPaperError,
    SleeveOutcome,
    compare_paper_sleeves,
)


T0 = datetime(2026, 11, 2, 4, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(minutes=1)
T2 = T0 + timedelta(hours=6)


def _signal(signal_id: str, *, weight: float = 0.25) -> EligibleSignal:
    return EligibleSignal(signal_id, date(2026, 11, 2), f"NSE:{signal_id}", T0, weight,
                          "a" * 64, "b" * 64, "c" * 64)


def _auto(signal_id: str, contribution: float, cost: float = 0.0) -> SleeveOutcome:
    return SleeveOutcome(signal_id, "TAKE", "FILLED", "system:auto-v1", T1, T2,
                         contribution + cost, cost)


def _human(signal_id: str, action: str, status: str, contribution: float,
           actor: str = "trader:ramesh", cost: float = 0.0) -> SleeveOutcome:
    gross = None if status == "UNKNOWN" else contribution + cost
    return SleeveOutcome(signal_id, action, status, actor, T1, T2, gross, cost)


def test_same_frozen_signals_keep_rejected_opportunities_in_cash_denominator():
    eligible = (_signal("S1"), _signal("S2"))
    report = compare_paper_sleeves(
        eligible=eligible,
        automatic=(_auto("S1", 0.02), _auto("S2", -0.01)),
        human=(_human("S1", "APPROVE", "FILLED", 0.015),
               _human("S2", "REJECT", "NOT_TAKEN", 0.0)),
        cash_return_by_signal={"S1": 0.0, "S2": 0.0},
    )
    assert report["complete"] is True
    assert report["promotion_eligible"] is False
    assert report["sessions"][0]["automatic_net_return"] == pytest.approx(0.01)
    assert report["sessions"][0]["human_net_return"] == pytest.approx(0.015)
    assert report["sessions"][0]["human_minus_automatic"] == pytest.approx(0.005)
    assert len(report["signal_set_sha256"]) == 64
    assert len(report["report_sha256"]) == 64


def test_no_trade_uses_signal_weighted_cash_return():
    eligible = (_signal("S1", weight=0.4),)
    report = compare_paper_sleeves(
        eligible=eligible,
        automatic=(SleeveOutcome("S1", "NO_TRADE", "NOT_TAKEN", "system:auto-v1", T1, T2, 0.004, 0.0),),
        human=(_human("S1", "WATCH", "NOT_TAKEN", 0.004),),
        cash_return_by_signal={"S1": 0.01},
    )
    assert report["sessions"][0]["automatic_net_return"] == pytest.approx(0.004)
    assert report["sessions"][0]["human_net_return"] == pytest.approx(0.004)


def test_net_is_reconciled_from_gross_and_explicit_cost_contributions():
    eligible = (_signal("S1"),)
    report = compare_paper_sleeves(
        eligible=eligible, automatic=(_auto("S1", 0.01, cost=0.005),),
        human=(_human("S1", "APPROVE", "FILLED", 0.006, cost=0.004),),
        cash_return_by_signal={"S1": 0.0},
    )
    row = report["sessions"][0]
    assert row["automatic_gross_return"] == pytest.approx(0.015)
    assert row["automatic_cost_contribution"] == pytest.approx(0.005)
    assert row["automatic_net_return"] == pytest.approx(0.01)
    assert row["human_net_return"] == pytest.approx(0.006)


def test_missing_or_duplicate_sleeve_rows_refuse_instead_of_shrinking_denominator():
    signals = (_signal("S1"), _signal("S2"))
    with pytest.raises(PairedPaperError, match="missing human outcome"):
        compare_paper_sleeves(eligible=signals, automatic=(_auto("S1", 0.0), _auto("S2", 0.0)),
                              human=(_human("S1", "APPROVE", "FILLED", 0.0),),
                              cash_return_by_signal={"S1": 0.0, "S2": 0.0})
    with pytest.raises(PairedPaperError, match="uniquely match"):
        compare_paper_sleeves(eligible=signals, automatic=(_auto("S1", 0.0), _auto("S2", 0.0)),
                              human=(_human("S1", "APPROVE", "FILLED", 0.0),
                                     _human("S1", "REJECT", "NOT_TAKEN", 0.0)),
                              cash_return_by_signal={"S1": 0.0, "S2": 0.0})


def test_unknown_outcomes_are_counted_and_report_is_not_complete():
    eligible = (_signal("S1"),)
    report = compare_paper_sleeves(
        eligible=eligible,
        automatic=(SleeveOutcome("S1", "TAKE", "UNKNOWN", "system:auto-v1", T1, T2, None, 0.0),),
        human=(_human("S1", "APPROVE", "UNKNOWN", 0.0),),
        cash_return_by_signal={"S1": 0.0},
    )
    assert report["unknown_outcome_count"] == 2
    assert report["complete"] is False
    assert report["sessions"][0]["automatic_net_return"] is None
    assert report["sessions"][0]["human_net_return"] is None


@pytest.mark.parametrize("mutate", [
    lambda row: replace(row, actor="agent:assistant"),
    lambda row: replace(row, decided_at=T0 - timedelta(seconds=1)),
    lambda row: replace(row, outcome_available_at=T1),
    lambda row: replace(row, action="REJECT", status="FILLED"),
    lambda row: replace(row, gross_nav_contribution=0.5),
])
def test_invalid_human_or_post_decision_outcome_contract_is_refused(mutate):
    signal = _signal("S1", weight=0.4)
    valid = _human("S1", "REJECT", "NOT_TAKEN", 0.0)
    with pytest.raises(PairedPaperError):
        compare_paper_sleeves(eligible=(signal,), automatic=(_auto("S1", 0.0),),
                              human=(mutate(valid),), cash_return_by_signal={"S1": 0.0})


def test_overallocated_frozen_opportunity_weights_are_rejected():
    with pytest.raises(PairedPaperError, match="exceed sleeve NAV"):
        compare_paper_sleeves(eligible=(_signal("S1", weight=0.6), _signal("S2", weight=0.6)),
                              automatic=(_auto("S1", 0.0), _auto("S2", 0.0)),
                              human=(_human("S1", "APPROVE", "FILLED", 0.0),
                                     _human("S2", "APPROVE", "FILLED", 0.0)),
                              cash_return_by_signal={"S1": 0.0, "S2": 0.0})
