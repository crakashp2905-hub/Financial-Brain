from __future__ import annotations

import pytest

from financial_brain.kernel.batch20c_agent import (
    ReadOnlyStore,
    claim,
    classify_intent,
    fallback_answer,
    groundedness,
    insufficient_data_message,
    lock_ok,
)
from financial_brain.kernel.batch20c_strategies import (
    StrategyCard,
    carry_annualised,
    cross_sectional_momentum,
    decompose,
    donchian_signal,
    drift_alert,
    failed_breakout_candidate,
    forward_evaluation,
    param_distribution,
    publish_trials,
    quantile_coverage,
    regret,
)
from financial_brain.kernel.contracts import EvidenceTier


def test_momentum_ranks_only_when_coverage_is_enough() -> None:
    returns = {"A": 0.1, "B": 0.05, "C": -0.02}
    universe = {"A", "B", "C", "D"}
    ranked, ok = cross_sectional_momentum(returns, universe, min_coverage=0.75)
    assert ok is True
    assert ranked == [("A", 0.1), ("B", 0.05), ("C", -0.02)]
    assert cross_sectional_momentum(returns, universe, min_coverage=0.9) == ([], False)


def test_donchian_uses_prior_bars_only() -> None:
    close = [1.0, 2.0, 3.0, 4.0, 1.0]
    assert donchian_signal(close, close, close, n=2) == [0, 0, 1, 1, -1]


def test_failed_breakout_is_a_candidate_not_a_signal() -> None:
    assert failed_breakout_candidate([10.0, 11.0, 9.0, 9.5], level=10.0, k=2) is True
    assert failed_breakout_candidate([10.0, 11.0, 12.0, 13.0], level=10.0, k=2) is False


def test_carry_is_annualised_from_the_roll_gap() -> None:
    assert carry_annualised(100.0, 102.0, 91) == pytest.approx(0.0802198, rel=1e-4)
    with pytest.raises(ValueError):
        carry_annualised(100.0, 102.0, 0)


def test_strategy_card_requires_rationale_hypothesis_and_invalidation() -> None:
    card = StrategyCard("orb", "open range", "range breaks persist", "close back inside range", EvidenceTier.UNSIGNED_REPLAY, ("failed on 2024",))
    assert card.negative_results == ("failed on 2024",)
    with pytest.raises(ValueError, match="invalidation"):
        StrategyCard("orb", "r", "e", "  ", EvidenceTier.UNSIGNED_REPLAY, ())


def test_decomposition_by_regime_and_market() -> None:
    out = decompose([0.01, -0.02, 0.03], ["up", "down", "up"], ["IN", "IN", "US"])
    assert out["regime"]["up"] == pytest.approx(0.02)
    assert out["regime"]["down"] == pytest.approx(-0.02)
    assert out["market"]["IN"] == pytest.approx(-0.005)
    assert out["market"]["US"] == pytest.approx(0.03)


def test_walk_forward_parameter_distribution_and_mode() -> None:
    counts, mode = param_distribution(["10", "10", "20"])
    assert counts == {"10": 2, "20": 1}
    assert mode == "10"


def test_quantile_coverage_counts_outcomes_at_or_below_forecast() -> None:
    assert quantile_coverage([0.5, 0.9], [0.4, 1.0]) == pytest.approx(0.5)


def test_drift_alert_flags_a_large_mean_shift() -> None:
    baseline = [0.0, 0.01, -0.01, 0.0]
    assert drift_alert(baseline, [0.05, 0.06, 0.04, 0.05], z_threshold=3.0) is True
    assert drift_alert(baseline, [0.0, 0.01, 0.0, -0.01], z_threshold=3.0) is False


def test_regret_is_best_alternative_minus_chosen() -> None:
    assert regret(5.0, {"a": 8.0, "b": 3.0}) == pytest.approx(3.0)
    with pytest.raises(ValueError):
        regret(5.0, {})


def test_forward_evidence_must_not_overlap_training() -> None:
    assert forward_evaluation({"p1", "p2"}, {"p3"}, [0.02]) == pytest.approx(0.02)
    with pytest.raises(ValueError, match="overlaps"):
        forward_evaluation({"p1", "p2"}, {"p2"}, [0.02])


def test_every_trial_is_published_including_failures() -> None:
    trials = [{"id": "t2", "outcome": "PASSED"}, {"id": "t1", "outcome": "FAILED"}]
    assert [t["id"] for t in publish_trials(trials)] == ["t1", "t2"]
    with pytest.raises(ValueError, match="every trial"):
        publish_trials([{"id": "t3"}])


def test_store_is_read_only_and_returns_copies() -> None:
    store = ReadOnlyStore({"px": [1, 2]})
    value = store.get("px")
    value.append(9)
    assert store.get("px") == [1, 2]
    with pytest.raises(PermissionError):
        store.set("px", [0])


def test_intent_classification_precedence() -> None:
    assert classify_intent("compare NIFTY and BANKNIFTY") == "COMPARE"
    assert classify_intent("screen for low volatility") == "SCREEN"
    assert classify_intent("draft a paper plan") == "PAPER_DRAFT"
    assert classify_intent("research a hypothesis") == "RESEARCH"
    assert classify_intent("why did it lose?") == "ASK"
    assert classify_intent("hello") == "UNKNOWN"


def test_insufficient_data_states_the_gap_without_speculating() -> None:
    assert insufficient_data_message(252, 100) == "Insufficient data: need 252 observations, have 100."


def test_groundedness_is_share_of_numbers_found_in_source() -> None:
    assert groundedness(["12.5", "7"], "gain 12.5 and 9 trades") == pytest.approx(0.5)


def test_fallback_is_deterministic_and_rule_based() -> None:
    assert fallback_answer("compare A and B") == fallback_answer("compare A and B")
    assert "same window" in fallback_answer("compare A and B")


def test_claims_must_carry_a_known_kind() -> None:
    assert claim("tested on 2024 data", "HISTORICAL") == {"text": "tested on 2024 data", "kind": "HISTORICAL"}
    with pytest.raises(ValueError, match="unknown claim kind"):
        claim("maybe works", "GUESS")


def test_lockfile_requires_exact_pins() -> None:
    assert lock_ok(["numpy==1.26.4", "# comment", "pandas==2.2.2"]) is True
    assert lock_ok(["numpy>=1.0"]) is False
    assert lock_ok([]) is False
