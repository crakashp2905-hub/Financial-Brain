from dataclasses import replace

import pytest

from financial_brain.forward_edge.fixed_look import (
    FixedLookError,
    FixedLookPolicy,
    evaluate_look,
)


def _policy():
    return FixedLookPolicy(
        policy_id="trial-fixed-looks-v1", checkpoints=(20, 40, 60), familywise_alpha=0.05,
        max_drawdown_floor=-0.15, frozen_at_utc="2026-01-01T00:00:00Z",
        first_observation_at_utc="2026-01-02T00:00:00Z",
    )


def test_unscheduled_interim_display_cannot_produce_an_efficacy_decision():
    result = evaluate_look(
        _policy(), completed_sessions=21, lower_confidence_bound=0.10,
        observed_max_drawdown=-0.02,
    )
    assert result["status"] == "UNSCHEDULED_NO_EFFICACY_DECISION"
    assert result["efficacy_decision"] == "WITHHELD"
    assert result["registered_look"] is False
    assert result["orders_permission"] == "UNCHANGED_BY_THIS_REPORT"


def test_registered_look_uses_predeclared_familywise_alpha_spending():
    result = evaluate_look(
        _policy(), completed_sessions=40, lower_confidence_bound=0.001,
        observed_max_drawdown=-0.04,
    )
    assert result["status"] == "REGISTERED_LOOK"
    assert result["efficacy_decision"] == "BOUND_POSITIVE"
    assert result["look_index"] == 2
    assert result["per_look_alpha"] == pytest.approx(0.05 / 3)
    assert result["orders_permission"] == "UNCHANGED_BY_THIS_REPORT"


def test_fixed_look_does_not_confirm_a_nonpositive_bound():
    result = evaluate_look(
        _policy(), completed_sessions=20, lower_confidence_bound=0.0,
        observed_max_drawdown=-0.01,
    )
    assert result["efficacy_decision"] == "NOT_CONFIRMED"


def test_risk_harm_stops_immediately_even_between_registered_looks():
    result = evaluate_look(
        _policy(), completed_sessions=23, lower_confidence_bound=0.5,
        observed_max_drawdown=-0.15,
    )
    assert result["status"] == "STOP_FOR_RISK_HARM"
    assert result["orders_permission"] == "DISABLED_FOR_RISK_HARM"
    assert result["efficacy_decision"] == "WITHHELD"


def test_missing_risk_evidence_fails_closed_and_withholds_efficacy():
    result = evaluate_look(
        _policy(), completed_sessions=20, lower_confidence_bound=0.5,
        observed_max_drawdown=None,
    )
    assert result["status"] == "RISK_EVIDENCE_MISSING"
    assert result["orders_permission"] == "NOT_AUTHORIZED"
    assert result["efficacy_decision"] == "WITHHELD"


def test_policy_is_frozen_before_first_observation_and_hashes_all_look_controls():
    policy = _policy()
    assert policy.per_look_alpha == pytest.approx(0.05 / 3)
    assert policy.sha256() != replace(policy, max_drawdown_floor=-0.2).sha256()
    with pytest.raises(FixedLookError, match="before the first observation"):
        replace(policy, frozen_at_utc="2026-01-02T00:00:00Z")
    with pytest.raises(FixedLookError, match="increasing"):
        replace(policy, checkpoints=(20, 20, 40))


def test_invalid_look_observations_are_rejected():
    with pytest.raises(FixedLookError, match="finite nonpositive"):
        evaluate_look(_policy(), completed_sessions=20, lower_confidence_bound=0.1,
                      observed_max_drawdown=0.01)
    with pytest.raises(FixedLookError, match="lower confidence bound must be finite"):
        evaluate_look(_policy(), completed_sessions=19, lower_confidence_bound=float("nan"),
                      observed_max_drawdown=-0.01)
