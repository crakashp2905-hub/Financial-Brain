from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forecasting.index_15m import (
    SeriesError,
    bars_15m,
    brier,
    check_probabilities,
    completed_before,
    features_at,
    forward_return,
    frequency_baseline,
    label,
    leak_check,
    noise_threshold,
    purged_splits,
)

IST = timezone(timedelta(hours=5, minutes=30))
OPEN = datetime(2026, 11, 17, 9, 15, tzinfo=IST)
CLOSE = datetime(2026, 11, 17, 15, 30, tzinfo=IST)


def minutes(n, start=100.0, drift=0.01, skip=()):
    return [(OPEN + timedelta(minutes=i), start + drift * i) for i in range(n) if i not in skip]


def test_full_bars_aligned_to_open():
    b = bars_15m(minutes(60), OPEN, CLOSE)
    assert len(b) == 4 and all(x.complete for x in b)
    assert b[0].start == OPEN and b[0].end == OPEN + timedelta(minutes=15)
    assert b[0].open == 100.0 and b[0].close == pytest.approx(100.14)


def test_missing_minute_makes_partial_bar():
    b = bars_15m(minutes(30, skip={20}), OPEN, CLOSE)
    assert b[0].complete and not b[1].complete


def test_session_end_partial_bar_is_not_full():
    close = OPEN + timedelta(minutes=20)
    b = bars_15m(minutes(20), OPEN, close)
    assert b[-1].end == close and b[-1].complete   # 5-minute final bar fully observed
    assert len(b) == 2


def test_naive_session_refused():
    with pytest.raises(SeriesError):
        bars_15m([], OPEN.replace(tzinfo=None), CLOSE)


def test_only_completed_bars_known():
    b = bars_15m(minutes(60), OPEN, CLOSE)
    assert len(completed_before(b, OPEN + timedelta(minutes=31))) == 2


def test_forward_return_and_labels():
    b = bars_15m(minutes(60), OPEN, CLOSE)
    r = forward_return(b, 0)
    assert r == pytest.approx(b[1].close / b[0].close - 1)
    assert forward_return(b, 3) is None
    assert label(0.002, 0.001) == "UP" and label(-0.002, 0.001) == "DOWN"
    assert label(0.0005, 0.001) == "FLAT" and label(None, 0.001) is None


def test_forward_return_none_across_partial_bar():
    b = bars_15m(minutes(45, skip={20}), OPEN, CLOSE)
    assert forward_return(b, 0) is None


def test_noise_threshold_from_training_only():
    assert noise_threshold([0.001, -0.002, 0.003]) == 0.001


def test_features_do_not_leak_future_bars():
    b = bars_15m(minutes(120), OPEN, CLOSE)
    at = OPEN + timedelta(minutes=45)
    assert leak_check(b, at, OPEN)
    f = features_at(b, at, OPEN)
    assert f["minutes_since_open"] == 45


def test_features_need_two_bars():
    b = bars_15m(minutes(15), OPEN, CLOSE)
    with pytest.raises(SeriesError):
        features_at(b, OPEN + timedelta(minutes=15), OPEN)


def test_purged_splits_embargo():
    for train, test in purged_splits(100, 5, label_steps=4, embargo=2):
        assert max(train, default=-10) + 6 < test[0]
        assert all(i < test[0] for i in train)


def test_frequency_baseline_and_probabilities():
    p = frequency_baseline(["UP", "UP", "DOWN", "FLAT"])()
    check_probabilities(p)
    assert p["UP"] == 0.5
    assert brier(p, "UP") == pytest.approx(0.25 + 0.0625 + 0.0625)
    with pytest.raises(SeriesError):
        check_probabilities({"UP": 0.5, "DOWN": 0.6, "FLAT": 0.0})
