import pytest

from financial_brain.forecasting.index_coverage import (
    ForecastCoverageError,
    score_expected_intervals,
)


def test_coverage_score_keeps_abstentions_misses_and_missing_rows_in_denominator():
    expected = [f"interval-{index}" for index in range(5)]
    issues = [
        {"interval_end": expected[0], "session_date": "2026-11-25", "status": "ISSUED",
         "probabilities": {"UP": .8, "DOWN": .1, "FLAT": .1}},
        {"interval_end": expected[1], "session_date": "2026-11-25", "status": "ABSTAINED",
         "probabilities": {"UP": .1, "DOWN": .1, "FLAT": .8}},
        {"interval_end": expected[2], "session_date": "2026-11-25", "status": "MISSED"},
        {"interval_end": expected[3], "session_date": "2026-11-25", "status": "ISSUED",
         "probabilities": {"UP": .8, "DOWN": .1, "FLAT": .1}},
    ]
    outcomes = [
        {"interval_end": expected[0], "actual": "UP", "realized_return": .01},
        {"interval_end": expected[1], "actual": "FLAT", "realized_return": 0.0},
        {"interval_end": expected[2], "actual": "DOWN", "realized_return": -.01},
        {"interval_end": expected[4], "actual": "UP", "realized_return": .02},
    ]
    report = score_expected_intervals(expected, issues, outcomes, bootstrap_reps=30)
    assert report["expected_intervals"] == 5
    assert report["issue_coverage"] == .8
    assert report["actionable_issue_coverage"] == .4
    assert report["abstention_rate"] == .2
    assert report["status_counts"] == {"ABSTAINED": 1, "ISSUED": 2, "MISSED": 1, "NO_ISSUE": 1}
    assert report["outcome_annotations"] == {
        "MISSED_WITH_OUTCOME": 1, "NO_ISSUE_WITH_OUTCOME": 1, "UNRESOLVED_OUTCOME": 1}
    assert report["scored_issued_and_abstained"]["metrics"]["n"] == 2
    assert report["scored_actionable_issued_only"]["metrics"]["n"] == 1
    assert report["missing_outcomes_are_not_successes"] is True


def test_coverage_scorer_rejects_duplicate_or_unexpected_issue_status():
    with pytest.raises(ForecastCoverageError, match="duplicate issue"):
        score_expected_intervals(["a"], [{"interval_end": "a"}, {"interval_end": "a"}], [])
    with pytest.raises(ForecastCoverageError, match="unsupported issue status"):
        score_expected_intervals(["a"], [{"interval_end": "a", "status": "OK"}], [])
