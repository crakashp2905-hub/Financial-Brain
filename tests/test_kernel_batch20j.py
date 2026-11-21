from __future__ import annotations

import pytest

from financial_brain.kernel.batch20j_alerts import (
    backoff_schedule,
    days_to_expiry_reminder,
    evaluate_alert,
    is_due,
    match_rules,
    paper_submit,
    run_workflow,
)
from financial_brain.kernel.batch20j_risk_learn import (
    aggregate_greeks,
    budget_guard,
    currency_breaches,
    drift_response,
    emergency_policy,
    explain_prediction,
    feature_drift,
    holdout_guard,
    limit_breaches,
    missing_features,
    paired_eval,
    rejection_learning,
    sanitize_rows,
)


def test_limits_report_each_breach() -> None:
    assert limit_breaches(0.2, 0.1, 0.3, 0.25, 0.05, 0.1) == ["ADV", "CONCENTRATION"]
    assert limit_breaches(0.05, 0.1, 0.1, 0.25, -0.2, 0.1) == ["GAP"]


def test_greeks_aggregate_quantity_weighted() -> None:
    totals = aggregate_greeks([(10, 0.5, 0.02, 0.1), (-5, 0.3, 0.04, 0.2)])
    assert totals["delta"] == pytest.approx(3.5)
    assert totals["gamma"] == pytest.approx(0.0)
    assert totals["vega"] == pytest.approx(0.0)


def test_correlated_currency_group_breach() -> None:
    out = currency_breaches({"EUR": 60.0, "CHF": -50.0, "JPY": 5.0}, {"EUR_BLOC": ["EUR", "CHF"]}, limit=100.0)
    assert out == {"EUR_BLOC": 110.0}


def test_emergency_policy_unwinds_only_excess_unhedged() -> None:
    assert emergency_policy(50, 20, max_unhedged=10) == "UNWIND_EXCESS"
    assert emergency_policy(50, 20, max_unhedged=40) == "HOLD"


def test_rejection_learning_uses_only_rejected_trades() -> None:
    assert rejection_learning([(True, 10.0), (False, 100.0), (True, 20.0)]) == pytest.approx(15.0)
    assert rejection_learning([(False, 5.0)]) is None


def test_feature_drift_reports_mean_shift() -> None:
    assert feature_drift({"x": [1.0, 3.0]}, {"x": [2.0, 4.0]}) == {"x": pytest.approx(1.0)}


def test_poisoned_rows_are_dropped_and_counted() -> None:
    kept, dropped = sanitize_rows([([1.0], 1), ([float("nan")], 0), ([2.0], 7), ([3.0], 0)])
    assert len(kept) == 2 and dropped == 2


def test_champion_challenger_pairing() -> None:
    out = paired_eval([0.5, 0.4, 0.6], [0.3, 0.45, 0.2])
    assert out["challenger_wins"] == 2.0
    assert out["mean_improvement"] == pytest.approx((0.2 - 0.05 + 0.4) / 3)


def test_holdout_cannot_be_reused() -> None:
    assert holdout_guard({"h1"}, {"h2"}) is True
    with pytest.raises(ValueError, match="already used"):
        holdout_guard({"h1"}, {"h1", "h2"})


def test_artifact_needs_features_the_runtime_has() -> None:
    assert missing_features(["a", "b", "c"], ["a", "c"]) == ["b"]


def test_prediction_explanation_needs_all_four_parts() -> None:
    text = explain_prediction(0.6, 0.1, 0.02, 0.05)
    assert "prediction=0.6" in text and "risk=0.05" in text
    with pytest.raises(ValueError, match="missing"):
        explain_prediction(0.6, None, 0.02, 0.05)


def test_drift_abstains_or_reduces_risk_past_threshold() -> None:
    assert drift_response(0.25, 0.1) == "ABSTAIN"
    assert drift_response(0.12, 0.1) == "REDUCE_RISK"
    assert drift_response(0.05, 0.1) == "NORMAL"


def test_exploration_budget_is_enforced() -> None:
    assert budget_guard(3, 10) == 7
    with pytest.raises(RuntimeError):
        budget_guard(10, 10)


def test_rules_match_with_wildcards() -> None:
    rules = [{"name": "nifty_only", "asset": "NIFTY"}, {"name": "any_breakout", "strategy": "breakout"}]
    assert match_rules(rules, {"asset": "NIFTY", "strategy": "gap"}) == ["nifty_only"]


def test_time_alerts_respect_session_and_weekend() -> None:
    assert is_due(600, 555, 930, weekday=1) is True
    assert is_due(600, 555, 930, weekday=6) is False
    assert is_due(200, 555, 930, weekday=1) is False


def test_alert_kinds_are_closed() -> None:
    assert evaluate_alert("PRICE", 105.0, 100.0) is True
    assert evaluate_alert("VOLUME", 50.0, 100.0) is False
    with pytest.raises(ValueError):
        evaluate_alert("NEWS", 1.0, 0.0)


def test_paper_automation_respects_veto_and_cap() -> None:
    assert paper_submit(1000.0, cap=5000.0, risk_veto=False) == "SUBMITTED_PAPER"
    assert paper_submit(9000.0, cap=5000.0, risk_veto=False) == "CAP_EXCEEDED"
    assert paper_submit(1000.0, cap=5000.0, risk_veto=True) == "VETOED"


def test_workflow_stops_at_first_failure() -> None:
    def boom() -> None:
        raise RuntimeError("fail")

    done, failed = run_workflow([("signal", lambda: None), ("paper", boom), ("journal", lambda: None)])
    assert done == ["signal"] and failed == "paper"


def test_retry_backoff_is_bounded_and_dead_letters() -> None:
    out = backoff_schedule(base=1.0, attempts=5, cap=5.0)
    assert out["delays"] == [1.0, 2.0, 4.0, 5.0, 5.0]
    assert out["dead_letter"] is True


def test_expiry_reminder_inside_window() -> None:
    assert days_to_expiry_reminder(today=100, expiry=103, window_days=3) is True
    assert days_to_expiry_reminder(today=100, expiry=110, window_days=3) is False
