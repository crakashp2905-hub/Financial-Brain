"""Analytics, recognition delays, reference levels, missing outcomes, benchmark spec and descriptive model (wave 7)."""

from __future__ import annotations

import pytest

from financial_brain.patterns.analytics import (
    RECOGNITION_DELAY_BARS,
    benchmark_spec_sha256,
    export_formations_json,
    fit_completion_model,
    lifecycle_counts,
    missing_outcomes,
    observation_side,
    overlap_leaks,
    reference_levels,
    time_to_confirm,
    trend_context,
    version_count_audit,
    volume_contraction,
)
from financial_brain.patterns.detectors import Formation
from financial_brain.patterns.events_store import EventLog
from financial_brain.patterns.spec import EventState, PatternEvent, transition


def test_trend_context_uses_only_past_bars() -> None:
    closes = [100.0, 101.0, 103.0, 104.0, 110.0, 999.0]
    ctx = trend_context(closes, t=4, lookback=5)
    assert ctx["label"] == "UP"
    # appending a future bar must not change the context at t=4
    assert trend_context(closes + [1.0], t=4, lookback=5) == ctx
    with pytest.raises(ValueError):
        trend_context(closes, t=10, lookback=3)


def test_unknown_volume_is_none_not_zero() -> None:
    vols = [100.0, 100.0, 100.0, 100.0, None, 50.0, 50.0, 50.0]
    assert volume_contraction(vols, t=7, window=3) is None
    clean = [100.0] * 4 + [50.0] * 4
    assert volume_contraction(clean, t=7, window=4) == pytest.approx(0.5)


def test_bearish_observation_is_exit_only_for_long_holdings() -> None:
    assert observation_side("bearish", has_long_position=True) == "EXIT_ONLY"
    assert observation_side("bearish", has_long_position=False) == "NO_ACTION"
    assert observation_side("bullish", has_long_position=False) == "LONG_CANDIDATE"


def test_recognition_delays_are_declared_per_source() -> None:
    assert set(RECOGNITION_DELAY_BARS) == {"pivot", "completed_candle", "weekly", "market_close"}


def test_reference_levels_are_labelled_and_never_projected() -> None:
    f = Formation("rectangle", "1", 0, 10, geometry={"top": 104.0, "bottom": 99.0, "window": 20})
    out = reference_levels(f)
    assert out["label"] == "DESCRIPTIVE_REFERENCE"
    assert out["projected_target"] is None
    assert out["levels"] == {"top": 104.0, "bottom": 99.0}


def test_lifecycle_counts_and_time_to_confirm() -> None:
    fs = [
        Formation("rectangle", "1", 0, 10, confirmed_index=14, confirmed_direction="bullish"),
        Formation("rectangle", "1", 0, 12),
        Formation("double_bottom", "1", 0, 15, failed_index=18),
    ]
    assert lifecycle_counts(fs, now=20) == {"CANDIDATE": 1, "CONFIRMED": 1, "FAILED": 1}
    assert time_to_confirm(fs) == [4]


def test_export_is_deterministic_json() -> None:
    f = Formation("rectangle", "1", 0, 10, geometry={"top": 1.0})
    assert export_formations_json([f]) == export_formations_json([f])
    assert export_formations_json([f]).startswith("[")


def test_version_audit_shows_every_count_change() -> None:
    audit = version_count_audit({"rectangle": 4, "flag_bullish": 2}, {"rectangle": 5, "double_top": 1})
    assert audit["rectangle"]["delta"] == 1
    assert audit["flag_bullish"]["delta"] == -2
    assert audit["double_top"]["old"] == 0


def test_missing_outcomes_are_counted_under_an_explicit_policy() -> None:
    outcomes = {"a": 0.02, "b": None, "c": -0.02}
    counted = missing_outcomes(outcomes, policy="count_as_zero")
    excluded = missing_outcomes(outcomes, policy="exclude_and_report")
    assert counted["n_missing"] == 1 and excluded["n_missing"] == 1
    assert counted["mean"] == pytest.approx(0.0)
    assert excluded["mean"] == pytest.approx(0.0)
    with pytest.raises(ValueError):
        missing_outcomes(outcomes, policy="drop_silently")


def test_benchmark_spec_hash_is_pinned_across_runs() -> None:
    assert benchmark_spec_sha256() == benchmark_spec_sha256()
    assert len(benchmark_spec_sha256()) == 64


def test_embargo_shorter_than_horizon_leaks() -> None:
    assert overlap_leaks(embargo=2, label_horizon=5) is True
    assert overlap_leaks(embargo=5, label_horizon=5) is False


def test_descriptive_model_is_uncalibrated_and_empty_input_refuses() -> None:
    empty = fit_completion_model([])
    assert empty["status"] == "NO_CALIBRATION"
    model = fit_completion_model([("rectangle", 1), ("rectangle", 0), ("double_top", 0)])
    assert model["status"] == "UNCALIBRATED_DESCRIPTIVE"
    assert 0 < model["rates"]["rectangle"] < 1


def test_restart_recovery_every_state_is_exactly_once() -> None:
    """Interrupt at every state and replay: each version lands exactly once (CP-0334)."""
    base = PatternEvent(event_id="pev_r", spec_sha256="0" * 64, lineage="L", timeframe="1D",
                        candidate_known_at_utc="2026-01-05T10:00:00+00:00", status=EventState.CANDIDATE,
                        data_sha256="1" * 64)
    conf = transition(base, EventState.CONFIRMED, confirmed_at_utc="2026-01-06T10:00:00+00:00")
    inval = transition(conf, EventState.INVALIDATED, invalidated_at_utc="2026-01-07T10:00:00+00:00")
    log = EventLog()
    for version in (base, conf, inval):
        log.append(version)
        log.append(version)  # replay after an interruption
    assert len(log) == 3
    assert [v.status for v in log.history("pev_r")] == [EventState.CANDIDATE, EventState.CONFIRMED, EventState.INVALIDATED]
