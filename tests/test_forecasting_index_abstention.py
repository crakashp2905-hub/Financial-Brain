from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from financial_brain.forecasting.index_abstention import (
    IndexAbstentionError,
    apply_index_abstention_policy,
    calibrate_index_abstention,
)

IST = ZoneInfo("Asia/Kolkata")
MANIFEST = "a" * 64


def calibration_rows():
    rows = []
    for i in range(120):
        rows.append({"interval_end": f"high-{i}", "actual": "UP",
                     "p_up": .95, "p_down": .03, "p_flat": .02})
    for i in range(120):
        rows.append({"interval_end": f"low-{i}", "actual": "UP",
                     "p_up": .40, "p_down": .50, "p_flat": .10})
    return rows


def decision(i, *, confidence=.98, regimes=()):
    return {"interval_end": f"interval-{i}", "session": "2026-11-25",
            "decision_at": (datetime(2026, 11, 25, 9, 30, tzinfo=IST)
                            + timedelta(minutes=15 * i)).isoformat(),
            "p_up": confidence, "p_down": (1 - confidence) * .6,
            "p_flat": (1 - confidence) * .4, "regimes": regimes}


def test_calibration_cutoff_and_sequential_policy_abstain_with_diagnostics():
    policy = calibrate_index_abstention(calibration_rows(),
                                        calibration_manifest_sha256=MANIFEST,
                                        target_error_rate=.10, min_calibration_predictions=100,
                                        max_actions_per_session=1)
    assert policy.min_confidence == .95
    assert policy.calibration_error_upper_95 <= .10
    assert len(policy.sha256) == 64
    report = apply_index_abstention_policy([
        decision(0, confidence=.98),
        decision(1, confidence=.99),
        decision(2, confidence=.90),
        decision(3, confidence=.99, regimes=("UNUSUAL_OPTION_SPREAD",)),
    ], policy)
    actions = [row["action"] for row in report["decisions"]]
    assert actions == ["ISSUE_DIRECTIONAL_RESEARCH_SIGNAL", "ABSTAIN", "ABSTAIN", "ABSTAIN"]
    assert report["decisions"][1]["reason"] == "SESSION_ACTION_LIMIT"
    assert report["decisions"][2]["reason"] == "LOW_CALIBRATED_CONFIDENCE"
    assert report["decisions"][3]["reason"] == "CRITICAL_MARKET_REGIME"
    assert report["decisions"][0]["normalized_entropy"] < 1
    assert report["issued_fraction"] == .25


def test_policy_fails_closed_when_calibration_is_insufficient_or_rows_are_unsorted():
    with pytest.raises(IndexAbstentionError, match="cannot meet"):
        calibrate_index_abstention(calibration_rows()[:20],
                                   calibration_manifest_sha256=MANIFEST,
                                   min_calibration_predictions=100)
    policy = calibrate_index_abstention(calibration_rows(),
                                        calibration_manifest_sha256=MANIFEST,
                                        min_calibration_predictions=100)
    with pytest.raises(IndexAbstentionError, match="chronological"):
        apply_index_abstention_policy([decision(1), decision(0)], policy)
