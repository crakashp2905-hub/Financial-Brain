import pytest

from financial_brain.evaluation.intraday_selectivity import SelectivityError, fit_selectivity_policy


def _candidate(event, day, symbol, score, gross, costs=2, stratum="large"):
    return {
        "event_id": f"candidate-{event}", "partition": "TRAIN", "session_date": day,
        "time_bucket": "10:00", "stratum": stratum, "symbol": symbol,
        "score": score, "gross_return_bps": gross, "round_trip_cost_bps": costs,
    }


def _control(event, day, symbol, gross, distance=0.1, costs=2, stratum="large"):
    return {
        "event_id": f"control-{event}", "partition": "TRAIN", "session_date": day,
        "time_bucket": "10:00", "stratum": stratum, "symbol": symbol,
        "covariate_distance": distance, "gross_return_bps": gross, "round_trip_cost_bps": costs,
    }


def _fit(candidates=None, controls=None):
    candidates = candidates or [
        _candidate("1", "2026-01-05", "A", 0.4, 10),
        _candidate("2", "2026-01-06", "B", 0.8, 20),
        _candidate("3", "2026-01-07", "C", 0.9, 10),
    ]
    controls = controls or [
        _control("1", "2026-01-05", "D", 7),
        _control("2", "2026-01-06", "E", 6),
        _control("3", "2026-01-07", "F", 15),
    ]
    return fit_selectivity_policy(
        candidates=candidates, controls=controls, thresholds=[0.3, 0.7, 0.85],
        training_start="2026-01-01", training_end="2026-01-31",
        candidate_source_sha256="a" * 64, control_source_sha256="b" * 64,
    )


def test_fit_uses_training_only_same_time_independent_controls_and_freezes_low_turnover_policy():
    policy = _fit()
    assert policy.threshold == 0.7
    assert policy.training_start == "2026-01-01" and policy.training_end == "2026-01-31"
    assert policy.matched_event_count == 2
    assert policy.selected_event_count == 2
    assert policy.sessions_with_selected_events == 2
    assert policy.mean_net_excess_bps == pytest.approx(4.5)
    assert policy.evidence_class == "TRAINING_ONLY_EXPLORATORY"
    assert policy.forward_schedule_sha256 is None
    assert policy.candidate_source_sha256 != policy.control_source_sha256
    assert policy.select(0.7) is True
    assert policy.select(0.69) is False
    assert len(policy.sha256) == 64


def test_forward_or_validation_rows_cannot_enter_threshold_selection():
    candidates = [_candidate("1", "2026-02-02", "A", 0.8, 10)]
    controls = [_control("1", "2026-02-02", "B", 5)]
    with pytest.raises(SelectivityError, match="outside the frozen training window"):
        _fit(candidates, controls)

    candidates = [_candidate("1", "2026-01-05", "A", 0.8, 10)]
    candidates[0]["partition"] = "FORWARD"
    controls = [_control("1", "2026-01-05", "B", 5)]
    with pytest.raises(SelectivityError, match="TRAIN partition"):
        _fit(candidates, controls)


def test_threshold_without_a_complete_independent_same_time_control_is_ineligible():
    candidates = [
        _candidate("1", "2026-01-05", "A", 0.4, 10),
        _candidate("2", "2026-01-06", "B", 0.8, 20),
    ]
    controls = [_control("2", "2026-01-06", "C", 5)]
    # Low threshold selects both and has an unmatched event; only the higher
    # threshold may be evaluated, with its independent same-time control.
    policy = _fit(candidates, controls)
    assert policy.threshold == 0.7
    assert policy.matched_event_count == policy.selected_event_count == 1


def test_control_must_be_different_symbol_and_same_time_and_stratum():
    candidates = [_candidate("1", "2026-01-05", "A", 0.8, 10)]
    controls = [
        _control("1a", "2026-01-05", "A", 1),
        {**_control("1b", "2026-01-05", "B", 1), "time_bucket": "11:00"},
        {**_control("1c", "2026-01-05", "C", 1), "stratum": "small"},
    ]
    with pytest.raises(SelectivityError, match="complete set of independent same-time controls"):
        _fit(candidates, controls)
