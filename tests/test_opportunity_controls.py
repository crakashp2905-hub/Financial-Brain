from __future__ import annotations

import pytest

from financial_brain.forward_edge.opportunity_controls import (
    MatchPolicy,
    OpportunityControlError,
    compare_matched_controls,
)


def _treated(trade_id, day="2026-11-12", **kw):
    return {"trade_id": trade_id, "session_date": day, "net_return_bp": -20.0,
            "notional_inr": 100000.0, "liquidity_bp": 15.0, "risk_budget_inr": 1000.0, **kw}


def _control(control_id, day="2026-11-12", **kw):
    return {"control_id": control_id, "session_date": day, "net_return_bp": 0.0,
            "notional_inr": 100000.0, "liquidity_bp": 15.0, "risk_budget_inr": 1000.0, **kw}


def test_matches_same_session_size_liquidity_and_risk_and_reports_net_difference():
    report = compare_matched_controls([_treated("t1")], [_control("cash" )],
                                      policy=MatchPolicy("frozen-v1"))
    assert report["matched_count"] == 1 and report["unmatched_count"] == 0
    assert report["pairs"][0]["control_id"] == "cash"
    assert report["pairs"][0]["net_difference_bp"] == -20.0
    assert len(report["causal_limitations"]) >= 3


def test_no_same_date_control_is_retained_as_unmatched_not_dropped():
    report = compare_matched_controls([_treated("t1")], [_control("other", "2026-11-13")],
                                      policy=MatchPolicy("frozen-v1"))
    assert report["treated_count"] == 1 and report["matched_count"] == 0
    assert report["pairs"][0]["reason"] == "NO_CONTEMPORANEOUS_CONTROL"


def test_caliper_misses_and_control_exhaustion_are_explicit():
    policy = MatchPolicy("strict-v1", max_size_relative_difference=0.05)
    missed = compare_matched_controls([_treated("t1")], [_control("large", notional_inr=200000)],
                                      policy=policy)
    assert missed["pairs"][0]["reason"] == "NO_CONTROL_WITHIN_CALIPERS"
    exhausted = compare_matched_controls([_treated("t1"), _treated("t2")], [_control("only-one")],
                                         policy=MatchPolicy("frozen-v1"))
    assert exhausted["matched_count"] == 1 and exhausted["unmatched_count"] == 1
    assert {row["reason"] for row in exhausted["pairs"] if row["status"] == "UNMATCHED"} == {
        "ELIGIBLE_CONTROLS_EXHAUSTED"
    }


def test_control_is_never_reused_and_each_treated_opportunity_is_preserved():
    report = compare_matched_controls([_treated("t1"), _treated("t2"), _treated("t3")],
                                      [_control("c1"), _control("c2")],
                                      policy=MatchPolicy("frozen-v1"))
    assert report["treated_count"] == 3 and len(report["pairs"]) == 3
    control_ids = [row["control_id"] for row in report["pairs"] if row["status"] == "MATCHED"]
    assert len(control_ids) == len(set(control_ids)) == 2
    assert report["unmatched_count"] == 1


def test_invalid_inputs_and_empty_treated_population_are_rejected():
    with pytest.raises(OpportunityControlError, match="required"):
        compare_matched_controls([], [], policy=MatchPolicy("v1"))
    with pytest.raises(OpportunityControlError, match="positive size/risk"):
        compare_matched_controls([_treated("t", risk_budget_inr=0)], [], policy=MatchPolicy("v1"))
    with pytest.raises(OpportunityControlError, match="max_risk"):
        MatchPolicy("bad", max_risk_relative_difference=-0.1)
