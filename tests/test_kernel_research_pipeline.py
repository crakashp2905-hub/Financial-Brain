from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.kernel.research_pipeline import (
    Job,
    JobState,
    LearningEvent,
    materialize_labels,
    run_bounded,
    windows,
)

T0 = datetime(2026, 11, 9, 4, 0, tzinfo=timezone.utc)


def test_realized_outcome_cannot_precede_the_horizon() -> None:
    with pytest.raises(ValueError, match="horizon"):
        LearningEvent("p1", "X", T0, T0 + timedelta(days=5), 0.1, 0.2, realized_at=T0 + timedelta(days=1))


def test_pending_labels_are_not_materialised_before_horizon() -> None:
    done = LearningEvent("p1", "X", T0, T0 + timedelta(days=1), 0.1, 0.2, realized_at=T0 + timedelta(days=1))
    pending = LearningEvent("p2", "X", T0, T0 + timedelta(days=9), 0.1)
    labels = materialize_labels([done, pending], now=T0 + timedelta(days=2))
    assert [e.prediction_id for e in labels] == ["p1"]


def test_expanding_and_rolling_windows_keep_an_embargo_gap() -> None:
    rolling = windows(n=20, train=5, test=5, embargo=2, mode="rolling")
    expanding = windows(n=20, train=5, test=5, embargo=2, mode="expanding")
    for train_r, test_r in rolling:
        assert test_r.start - train_r.stop == 2
    assert expanding[1][0].start == 0
    assert rolling[1][0].start == 5
    with pytest.raises(ValueError):
        windows(n=20, train=5, test=5, embargo=2, mode="random")


def test_run_bounded_keeps_input_order() -> None:
    results = run_bounded([lambda i=i: i * i for i in range(6)], max_workers=3)
    assert results == [0, 1, 4, 9, 16, 25]
    with pytest.raises(ValueError):
        run_bounded([lambda: 1], max_workers=0)


def test_job_cancel_semantics() -> None:
    job = Job("j1")
    job.start()
    job.cancel()
    assert job.state is JobState.CANCELLED
    done = Job("j2")
    done.start()
    done.finish()
    with pytest.raises(ValueError, match="cannot cancel"):
        done.cancel()
