from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from financial_brain.forecasting.index_15m import (
    SeriesError,
    brier,
    check_probabilities,
    completed_before,
    features_at,
    frequency_baseline,
    label,
    leak_check,
    noise_threshold,
    purged_splits,
)
from financial_brain.forecasting.index_market import IndexMinute, build_15m_bars, forward_return

IST = ZoneInfo("Asia/Kolkata")
OPEN = datetime(2026, 11, 17, 9, 15, tzinfo=IST)


def market_bars(*, count: int = 60, skip: set[int] = frozenset()):
    minutes = []
    for index in range(count):
        if index in skip:
            continue
        ts = OPEN + timedelta(minutes=index)
        price = 100.0 + index * 0.01
        minutes.append(IndexMinute(ts, ts + timedelta(seconds=2), price, price + 0.02,
                                   price - 0.02, price + 0.01))
    return build_15m_bars(minutes, as_of=OPEN + timedelta(minutes=count, seconds=5))


def test_features_reuse_original_contract_on_availability_aware_bars():
    bars = market_bars()
    decision = OPEN + timedelta(minutes=31)
    assert len(completed_before(bars, decision)) == 2
    features = features_at(bars, decision, OPEN)
    assert features["minutes_since_open"] == 31
    assert features["ret_15m"] > 0
    assert leak_check(bars, decision, OPEN)
    assert forward_return(bars, 0, horizon_bars=1) > 0


def test_feature_contract_refuses_unavailable_and_noncontiguous_bars():
    bars = market_bars(skip={20})
    with pytest.raises(SeriesError, match="contiguous"):
        features_at(bars, OPEN + timedelta(minutes=46), OPEN)
    with pytest.raises(SeriesError, match="timezone"):
        completed_before(bars, OPEN.replace(tzinfo=None))


def test_horizon_purge_baseline_and_brier_contracts():
    for train, test in purged_splits(100, 5, label_steps=4, embargo=2):
        assert max(train, default=-10) + 6 < test[0]
        assert all(index < test[0] for index in train)
    probabilities = frequency_baseline(["UP", "UP", "DOWN", "FLAT"])()
    check_probabilities(probabilities)
    assert probabilities["UP"] == 0.5
    assert brier(probabilities, "UP") == pytest.approx(0.375)
    assert label(0.002, 0.001) == "UP"
    assert label(-0.002, 0.001) == "DOWN"
    assert label(0.0005, 0.001) == "FLAT"
    assert label(None, 0.001) is None


@pytest.mark.parametrize("args", [(1, 5, 1, 0), (100, 1, 1, 0), (100, 5, 0, 0),
                                    (100, 5, 1, -1)])
def test_purged_splits_reject_invalid_parameters(args):
    with pytest.raises(SeriesError):
        purged_splits(*args)


def test_training_noise_threshold_and_probabilities_fail_closed():
    assert noise_threshold([0.001, -0.002, 0.003]) == 0.001
    with pytest.raises(SeriesError, match="training returns"):
        noise_threshold([])
    with pytest.raises(SeriesError, match="must be positive"):
        noise_threshold([0.0, 0.0])
    with pytest.raises(SeriesError, match="sum to one"):
        check_probabilities({"UP": 0.5, "DOWN": 0.6, "FLAT": 0.0})
