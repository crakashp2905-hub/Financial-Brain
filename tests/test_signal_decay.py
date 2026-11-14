from __future__ import annotations

import pytest

from financial_brain.evaluation.signal_decay import (
    SignalDecayError,
    SignalDecayPlan,
    analyze_signal_decay,
)


def _plan(**changes):
    values = {"plan_id": "decay-v1", "protocol_sha256": "a" * 64,
              "development_data_sha256": "b" * 64, "development_start": "2026-01-02",
              "development_end": "2026-01-03", "protected_final_start": "2026-01-04",
              "protected_final_end": "2026-02-01", "frozen_at_utc": "2026-01-01T00:00:00Z",
              "families": ("momentum",), "horizons": (1, 5, 10, 20)}
    values.update(changes)
    return SignalDecayPlan(**values)


def _rows(plan=None):
    plan = plan or _plan()
    means = {1: 0.01, 5: 0.04, 10: 0.08, 20: 0.02}
    rows = []
    for horizon in plan.horizons:
        for i, day in enumerate(("2026-01-02", "2026-01-03")):
            rows.append({"split": "DEVELOPMENT", "family": "momentum", "horizon_sessions": horizon,
                         "session_date": day, "decision_sha256": f"{i + 1:064x}",
                         "information_coefficient": means[horizon],
                         "net_alpha_fraction": 0.0 if horizon == 5 else (0.002 if i == 0 else 0.0),
                         "turnover_fraction": 0.01})
    return rows


def test_reports_time_to_peak_half_life_and_turnover_to_net_alpha_by_horizon():
    report = analyze_signal_decay(plan=_plan(), observations=_rows())
    family = report["families"]["momentum"]
    assert family["time_to_peak_information_horizon"] == 10
    assert family["signal_half_life_sessions"] == pytest.approx(16.67, abs=0.01)
    assert family["by_horizon"][1]["turnover_to_absolute_net_alpha"] == pytest.approx(10.0)
    assert family["by_horizon"][5]["turnover_alpha_status"] == "UNDEFINED_NEAR_ZERO_ALPHA"
    assert family["horizon_action"].startswith("NO_CHANGE")
    assert report["observation_count"] == len(report["observations"]) == 8


def test_final_window_and_non_development_rows_are_rejected():
    rows = _rows()
    rows[0]["session_date"] = "2026-01-04"
    with pytest.raises(SignalDecayError, match="outside the development"):
        analyze_signal_decay(plan=_plan(), observations=rows)
    rows = _rows()
    rows[0]["split"] = "FINAL"
    with pytest.raises(SignalDecayError, match="DEVELOPMENT"):
        analyze_signal_decay(plan=_plan(), observations=rows)


def test_missing_horizon_or_mismatched_session_schedule_is_rejected():
    rows = _rows()
    with pytest.raises(SignalDecayError, match="every family/horizon"):
        analyze_signal_decay(plan=_plan(), observations=rows[:-2])
    rows = _rows()
    rows[-1]["decision_sha256"] = "9" * 64
    with pytest.raises(SignalDecayError, match="same sessions"):
        analyze_signal_decay(plan=_plan(), observations=rows)


def test_invalid_metrics_and_plan_are_rejected():
    rows = _rows()
    rows[0]["turnover_fraction"] = -0.1
    with pytest.raises(SignalDecayError, match="turnover"):
        analyze_signal_decay(plan=_plan(), observations=rows)
    with pytest.raises(SignalDecayError, match="at least three"):
        _plan(horizons=(5, 10))
