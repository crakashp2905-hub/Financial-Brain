import pytest

from financial_brain.forecasting.index_skill import IndexSkillError, predictive_skill_gate


def cohorts(sessions=30, intervals_per_session=20):
    candidate, baseline = [], []
    for day in range(sessions):
        for number in range(intervals_per_session):
            actual = ("UP", "DOWN", "FLAT")[number % 3]
            key = f"{day}-{number}"
            candidate.append({"interval_end": key, "session": f"day-{day}", "actual": actual,
                              "p_up": .9 if actual == "UP" else .05,
                              "p_down": .9 if actual == "DOWN" else .05,
                              "p_flat": .9 if actual == "FLAT" else .05})
            baseline.append({"interval_end": key, "session": f"day-{day}", "actual": actual,
                             "p_up": 1 / 3, "p_down": 1 / 3, "p_flat": 1 / 3})
    return candidate, baseline


def test_predictive_gate_requires_paired_skill_and_never_reads_option_pnl():
    candidate, baseline = cohorts()
    report = predictive_skill_gate(candidate, {"class_frequency": baseline},
                                   bootstrap_reps=100, seed=11)
    assert report["status"] == "PREDICTIVE_SKILL_VERIFIED"
    assert report["options_pnl_considered"] is False
    comparison = report["comparisons"]["class_frequency"]
    assert comparison["status"] == "PASS"
    assert comparison["brier_skill_ci_95"]["lower"] > 0


def test_gate_fails_identical_or_incomparable_baselines():
    candidate, baseline = cohorts(sessions=4, intervals_per_session=4)
    identical = predictive_skill_gate(candidate, {"same": candidate}, minimum_rows=1,
                                      minimum_sessions=2, bootstrap_reps=100)
    assert identical["status"] == "NOT_VERIFIED"
    assert identical["comparisons"]["same"]["brier_skill_ci_95"]["lower"] == 0

    incomplete = predictive_skill_gate(candidate, {"missing_rows": baseline[:-1]},
                                       minimum_rows=1, minimum_sessions=2, bootstrap_reps=100)
    assert incomplete["status"] == "NOT_VERIFIED"
    assert incomplete["comparisons"]["missing_rows"]["status"] == "INCOMPARABLE"


def test_gate_rejects_invalid_or_nonprobabilistic_inputs():
    with pytest.raises(IndexSkillError, match="at least one hard baseline"):
        predictive_skill_gate([{"interval_end": "x"}], {})
    with pytest.raises(IndexSkillError, match="finite class probabilities"):
        predictive_skill_gate([{"interval_end": "x", "session": "d", "actual": "UP",
                                "p_up": 1, "p_down": 0, "p_flat": float("nan")}],
                              {"baseline": []})
