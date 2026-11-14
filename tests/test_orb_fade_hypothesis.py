from dataclasses import asdict

import pytest

from financial_brain.forward_edge.orb_fade_hypothesis import (
    ORB_FADE_HYPOTHESIS,
    OrbfadeError,
    cost_budget_scenarios,
    observe_failed_break,
)
from financial_brain.forward_edge.registry import Registry


def _observation(**updates):
    row = {
        "opening_range_available_at": "2026-11-12T09:30:00+05:30",
        "rejection_bar_event_at": "2026-11-12T09:35:00+05:30",
        "rejection_bar_available_at": "2026-11-12T09:35:01+05:30",
        "opening_range_high": "100", "opening_range_low": "99",
        "rejection_bar_close": "99.8", "probe_high": "100.2", "probe_low": "99.5",
    }
    return {**row, **updates}


def test_orb_fade_is_a_separate_unregistered_exploratory_mechanism_with_cost_policy():
    hypothesis = ORB_FADE_HYPOTHESIS
    assert hypothesis.evidence_class == "EXPLORATORY_RESEARCH"
    assert hypothesis.registered_as_evidence is False
    assert "failed price acceptance" in hypothesis.mechanism
    assert "2x" in hypothesis.cost_budget_policy
    assert set(hypothesis.cost_components) >= {"entry_spread", "exit_spread", "market_impact", "statutory_taxes"}
    registry = Registry()
    assert len(registry) == 0
    assert len(hypothesis.sha256) == 64
    assert asdict(hypothesis)["quantity"] is None
    assert asdict(hypothesis)["order_intent"] is None


def test_timestamp_safe_upper_and_lower_rejections_emit_candidates_without_orders():
    upper = observe_failed_break(_observation(), decision_at="2026-11-12T09:35:02+05:30")
    assert upper["direction"] == "FADE_SHORT"
    assert upper["status"] == "EXPLORATORY_CANDIDATE"
    assert upper["evidence_class"] == "EXPLORATORY_RESEARCH"
    assert upper["registered_as_evidence"] is False
    assert upper["quantity"] is None and upper["order_intent"] is None

    lower = observe_failed_break(_observation(
        rejection_bar_close="99.2", probe_high="99.5", probe_low="98.8"),
        decision_at="2026-11-12T09:35:02+05:30")
    assert lower["direction"] == "FADE_LONG"


@pytest.mark.parametrize("field,value", [
    ("rejection_bar_available_at", "2026-11-12T09:35:03+05:30"),
    ("rejection_bar_event_at", "2026-11-12T09:29:59+05:30"),
    ("opening_range_available_at", "2026-11-12T09:35:01+05:30"),
])
def test_future_or_not_yet_available_rejection_data_is_rejected(field, value):
    with pytest.raises(OrbfadeError, match="future or not-yet-available"):
        observe_failed_break(_observation(**{field: value}), decision_at="2026-11-12T09:35:02+05:30")


def test_cost_budget_prices_every_round_trip_component_under_cost_stress():
    report = cost_budget_scenarios(
        gross_edge_bps="50", entry_spread_bps="1", exit_spread_bps="2", fees_bps="3",
        taxes_bps="1", impact_bps="4", fee_schedule_id="NSE-EQ-2026-11",
        fee_schedule_sha256="a" * 64,
    )
    assert [row["round_trip_cost_bps"] for row in report["scenarios"]] == ["11", "16.5", "22"]
    assert all(row["passes_cost_budget"] for row in report["scenarios"])
    assert report["all_scenarios_pass"] is True
    assert report["registered_as_evidence"] is False
    assert report["quantity"] is None and report["order_intent"] is None

    no_margin = cost_budget_scenarios(
        gross_edge_bps="22", entry_spread_bps="1", exit_spread_bps="2", fees_bps="3",
        taxes_bps="1", impact_bps="4", fee_schedule_id="NSE-EQ-2026-11",
        fee_schedule_sha256="a" * 64,
    )
    assert no_margin["all_scenarios_pass"] is False


def test_missing_spread_fee_schedule_or_invalid_hash_cannot_pass_cost_gate():
    common = dict(gross_edge_bps="100", entry_spread_bps="1", exit_spread_bps="1",
                 fees_bps="1", taxes_bps="0", impact_bps="1",
                 fee_schedule_id="NSE-EQ", fee_schedule_sha256="a" * 64)
    with pytest.raises(OrbfadeError, match="entry_spread_bps"):
        cost_budget_scenarios(**{**common, "entry_spread_bps": None})
    with pytest.raises(OrbfadeError, match="fee schedule SHA-256"):
        cost_budget_scenarios(**{**common, "fee_schedule_sha256": "not-a-hash"})
