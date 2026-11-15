from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.regime.asof import (
    AsOfRegimeError,
    FEATURES,
    TimedFeature,
    classify_asof,
)


DECISION = datetime(2026, 11, 10, 4, 0, tzinfo=timezone.utc)
SOURCE = "a" * 64


def _features(at: datetime, *, values: dict | None = None) -> list[TimedFeature]:
    data = {
        "c": 100.0,
        "ma50": 98.0,
        "ma200": 95.0,
        "k200": 200,
        "ma200_slope": 0.02,
        "dd": -0.01,
        "ret20": 0.03,
        "rv20": 0.12,
        "vix": 18.0,
        "above_200": 0.62,
    }
    data.update(values or {})
    return [TimedFeature(name, data[name], at, at, SOURCE) for name in FEATURES]


def test_snapshot_uses_only_features_evented_and_available_by_decision() -> None:
    prior = DECISION - timedelta(days=1)
    morning = classify_asof(_features(prior), decision_utc=DECISION)
    assert morning.status == "AS_OF"
    assert morning.regime == "RISK_ON"
    assert morning.classifier_version == "v2"


def test_full_day_regime_and_future_prices_cannot_change_morning_snapshot() -> None:
    prior = _features(DECISION - timedelta(days=1))
    baseline = classify_asof(prior, decision_utc=DECISION)
    later_close = _features(
        DECISION + timedelta(hours=7),
        values={"c": 70.0, "ma50": 90.0, "ma200": 95.0, "k200": 200,
                "dd": -0.2, "ret20": -0.15, "rv20": 0.5, "vix": 35.0,
                "above_200": 0.2},
    )
    observed = classify_asof(prior + later_close, decision_utc=DECISION)
    assert observed.regime == baseline.regime
    assert observed.selected_inputs == baseline.selected_inputs
    assert observed.evidence_sha256 == baseline.evidence_sha256


def test_late_arriving_observation_is_not_usable_even_when_event_time_is_early() -> None:
    points = _features(DECISION - timedelta(minutes=5))
    points.append(TimedFeature("vix", 50.0, DECISION - timedelta(seconds=5),
                               DECISION + timedelta(seconds=1), "b" * 64))
    result = classify_asof(points, decision_utc=DECISION)
    selected_vix = next(item[1] for item in result.selected_inputs if item[0] == "vix")
    assert selected_vix == 18.0


def test_only_full_day_features_returns_blocked_snapshot() -> None:
    result = classify_asof(_features(DECISION + timedelta(hours=7)), decision_utc=DECISION)
    assert result.status == "BLOCKED_MISSING_AS_OF_FEATURES"
    assert result.regime is None
    assert set(result.missing_features) == set(FEATURES)


def test_bad_timestamp_order_or_source_hash_is_rejected() -> None:
    with pytest.raises(AsOfRegimeError, match="availability cannot precede"):
        classify_asof([
            TimedFeature("c", 1.0, DECISION, DECISION - timedelta(seconds=1), SOURCE)
        ], decision_utc=DECISION)
    with pytest.raises(AsOfRegimeError, match="SHA-256"):
        classify_asof([
            TimedFeature("c", 1.0, DECISION, DECISION, "not-a-hash")
        ], decision_utc=DECISION)
