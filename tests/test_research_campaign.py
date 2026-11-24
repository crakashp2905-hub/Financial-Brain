from __future__ import annotations

import pytest

from financial_brain.agents.research_campaign import (
    Campaign,
    Candidate,
    critique,
    seal_contract,
)


def candidate() -> Candidate:
    return Candidate(
        hypothesis_id="hyp-child-2",
        parent_hypothesis_id="hyp-parent-1",
        family_id="momentum-family",
        title="Fixture momentum variant",
        proposal="Timestamp point-in-time signal with fees and slippage; test trial family, universe and capacity; differs from prior failed baseline.",
        data_availability="Point-in-time availability timestamps are required.",
        mechanism="Momentum persistence after costs and market impact.",
        distinction_from_failures="Different from prior failed baseline by holding horizon.",
        evaluation_contract={"dataset": "fixture-v1", "cost_model": "fees-slippage-v1", "split": "fixed"},
    )


def test_fixture_campaign_is_deterministic_and_traceable() -> None:
    c = candidate()
    campaign = Campaign(allowed_context={"catalog": ("fixture-v1", "failed-hyp-0")})
    assert campaign.read_context("catalog") == ("fixture-v1", "failed-hyp-0")
    result = campaign.rehearse(c, {
        "contract_sha256": seal_contract(c.evaluation_contract),
        "metrics": {"net_return": -0.01, "drawdown": 0.04},
        "outcome": "NO_EDGE_FOUND",
    })
    assert result["outcome"] == "NO_EDGE_FOUND"
    assert result["execution_allowed"] is False
    assert result["hypothesis_id"] != result["parent_hypothesis_id"]
    assert result["critique"] == []
    assert campaign.verify_trace()


def test_no_live_or_unlisted_action_is_authorized() -> None:
    campaign = Campaign()
    assert not campaign.authorize("PLACE_ORDER")
    assert not campaign.authorize("READ_SECRET")
    assert not campaign.authorize("DELETE_AUDIT")
    assert campaign.verify_trace()


def test_contract_must_be_sealed_before_fixture_metrics_are_accepted() -> None:
    c = candidate()
    campaign = Campaign()
    with pytest.raises(ValueError, match="sealed evaluation contract"):
        campaign.run_fixture(c, {"contract_sha256": "bad", "metrics": {"sharpe": 99}})


def test_candidate_needs_lineage_and_research_rationale() -> None:
    c = candidate()
    invalid = Candidate(**{**c.__dict__, "hypothesis_id": c.parent_hypothesis_id})
    with pytest.raises(ValueError, match="new descendant"):
        critique(invalid)


def test_missing_critique_controls_are_reported() -> None:
    c = candidate()
    incomplete = Candidate(**{**c.__dict__, "proposal": "Buy when RSI is low.",
                              "data_availability": "daily data", "mechanism": "RSI rule",
                              "distinction_from_failures": "new RSI version"})
    assert {finding["check"] for finding in critique(incomplete)} == {
        "lookahead", "costs", "multiplicity", "survivorship", "capacity", "novelty"
    }


def test_tampered_event_breaks_hash_chain() -> None:
    campaign = Campaign()
    campaign.authorize("REQUEST_HUMAN_REVIEW")
    campaign.events[0]["payload"]["action"] = "PLACE_ORDER"
    assert not campaign.verify_trace()
