from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.degradation_monitor import (
    Action,
    DegradationError,
    DegradationMonitor,
    FrozenThresholds,
    RollingObservation,
)
from financial_brain.forward_edge.durable import DurableJournal


UTC = timezone.utc
START = datetime(2026, 11, 2, 3, 45, tzinfo=UTC)


def policy(**changes):
    values = dict(
        strategy_id="momentum-v4",
        strategy_version="sha256:strategy-4",
        frozen_at=START - timedelta(days=2),
        cohort_starts_at=START,
        pause_net_alpha_bps=0.0,
        retire_net_alpha_bps=-20.0,
        max_slippage_bps=12.0,
        min_fill_coverage=0.75,
        max_drift_score=0.30,
        min_observations=5,
        pause_after_breaches=2,
        retire_after_severe_breaches=3,
    )
    values.update(changes)
    return FrozenThresholds(**values)


def observation(p, name, *, alpha=8.0, slip=8.0, fill=0.9, drift=0.1,
                count=10, evidence=("quote-batch-17",), offset=1):
    end = START + timedelta(days=offset)
    return RollingObservation(name, p.policy_sha256, START, end, end + timedelta(minutes=1),
                              alpha, slip, fill, drift, count, evidence)


def test_policy_is_frozen_before_cohort_and_hash_is_stable():
    p = policy()
    assert p.policy_sha256 == policy().policy_sha256
    with pytest.raises(FrozenInstanceError):
        p.max_slippage_bps = 1  # type: ignore[misc]
    with pytest.raises(DegradationError, match="before the cohort"):
        policy(frozen_at=START)


def test_healthy_window_continues_and_breach_pauses_at_frozen_threshold():
    p = policy(pause_after_breaches=1)
    monitor = DegradationMonitor(p)
    assert monitor.observe(observation(p, "ok")).action == Action.CONTINUE
    alarm = monitor.observe(observation(p, "slip", alpha=5, slip=12.01, offset=2))
    assert alarm.action == Action.PAUSE
    assert "slippage crossed frozen maximum" in alarm.reasons


def test_pause_requires_persistent_breach_but_missing_evidence_fails_closed():
    p = policy()
    monitor = DegradationMonitor(p)
    assert monitor.observe(observation(p, "bad-1", alpha=-2)).action == Action.CONTINUE
    assert monitor.observe(observation(p, "bad-2", alpha=-2, offset=2)).action == Action.PAUSE
    assert monitor.observe(observation(p, "recovery", offset=3)).action == Action.PAUSE

    uncertain = DegradationMonitor(p)
    event = uncertain.observe(observation(p, "missing", evidence=()))
    assert event.action == Action.PAUSE
    assert "evidence IDs are missing" in event.reasons


def test_repeated_severe_alpha_retires_candidate():
    p = policy()
    monitor = DegradationMonitor(p)
    outcomes = [monitor.observe(observation(p, f"severe-{i}", alpha=-20 - i, offset=i + 1))
                for i in range(1, 4)]
    assert [e.action for e in outcomes] == [Action.CONTINUE, Action.PAUSE, Action.RETIRE]
    assert monitor.observe(observation(p, "later-recovery", offset=5)).action == Action.RETIRE


def test_duplicate_or_foreign_policy_observations_are_rejected():
    p = policy()
    monitor = DegradationMonitor(p)
    obs = observation(p, "unique")
    monitor.observe(obs)
    with pytest.raises(DegradationError, match="already recorded"):
        monitor.observe(obs)
    with pytest.raises(DegradationError, match="different frozen policy"):
        DegradationMonitor(policy(max_drift_score=0.31), monitor.events)
    wrong = observation(policy(max_drift_score=0.31), "foreign")
    with pytest.raises(DegradationError, match="does not attest"):
        monitor.observe(wrong)


def test_incomplete_or_nonfinite_metrics_never_continue():
    p = policy()
    monitor = DegradationMonitor(p)
    event = monitor.observe(observation(p, "short", count=4))
    assert event.action == Action.PAUSE
    next_monitor = DegradationMonitor(p)
    event = next_monitor.observe(observation(p, "nan", alpha=float("nan")))
    assert event.action == Action.PAUSE
    assert "net_alpha_bps is missing or non-finite" in event.reasons


def test_observations_must_be_complete_and_inside_the_frozen_cohort():
    p = policy()
    late = observation(p, "late")
    monitor = DegradationMonitor(p)
    with pytest.raises(DegradationError, match="not yet complete"):
        monitor.observe(RollingObservation(late.observation_id, late.policy_sha256, late.window_start,
                                           late.window_end, late.window_end - timedelta(seconds=1),
                                           late.net_alpha_bps, late.slippage_bps, late.fill_coverage,
                                           late.drift_score, late.sample_count, late.evidence_ids))


def test_durable_alarm_journal_recovers_counts_latch_and_duplicate_guard(tmp_path):
    p = policy()
    journal = DurableJournal(tmp_path / "alarms.jsonl")
    monitor = DegradationMonitor(p, journal=journal)
    first = monitor.observe(observation(p, "bad-1", alpha=-2))
    assert first.action == Action.CONTINUE

    restarted = DegradationMonitor(p, journal=DurableJournal(journal.path))
    second = restarted.observe(observation(p, "bad-2", alpha=-2, offset=2))
    assert second.action == Action.PAUSE
    recovered = DegradationMonitor(p, journal=DurableJournal(journal.path))
    assert recovered.events == (first, second)
    assert recovered.observe(observation(p, "recovery", offset=3)).action == Action.PAUSE
    with pytest.raises(DegradationError, match="already recorded"):
        recovered.observe(observation(p, "recovery", offset=3))
    assert [row["t"] for row in DurableJournal(journal.path).records()] == [
        "DEGRADATION_ALARM", "DEGRADATION_ALARM", "DEGRADATION_ALARM",
    ]


def test_failed_durable_alarm_append_does_not_advance_monitor_state(tmp_path, monkeypatch):
    p = policy()
    journal = DurableJournal(tmp_path / "alarms.jsonl")
    monitor = DegradationMonitor(p, journal=journal)

    def fail(_record):
        raise OSError("disk unavailable")

    monkeypatch.setattr(journal, "append", fail)
    obs = observation(p, "retryable", alpha=-2)
    with pytest.raises(OSError, match="disk unavailable"):
        monitor.observe(obs)
    assert monitor.events == ()

    monkeypatch.undo()
    assert monitor.observe(obs).action == Action.CONTINUE
    assert len(monitor.events) == 1
