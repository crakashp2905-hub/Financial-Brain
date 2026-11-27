from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from financial_brain.forecasting.index_15m import (
    SeriesError,
    brier,
    check_probabilities,
    completed_before,
    cost_aware_flat_band,
    features_at,
    frequency_baseline,
    label,
    leak_check,
    noise_threshold,
    previous_bar_baseline,
    purged_splits,
    purged_time_splits,
    random_walk_baseline,
    same_time_of_day_baseline,
    session_regimes,
    volatility_aware_no_direction_baseline,
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


def test_timestamp_purge_uses_actual_label_ends_and_keeps_cross_sections_together():
    decisions = [OPEN + timedelta(minutes=15 * i) for i in range(16) for _ in range(2)]
    ends = [decision + timedelta(minutes=(30 if int((decision - OPEN).total_seconds() // 900) % 2 else 15))
            for decision in decisions]
    folds = purged_time_splits(decisions, ends, folds=4, embargo=timedelta(minutes=15))
    assert folds
    for train, test in folds:
        test_start = decisions[test[0]]
        assert test
        assert len(test) % 2 == 0
        assert all(decisions[index] < test_start and ends[index] < test_start - timedelta(minutes=15)
                   for index in train)
        assert len({decisions[index] for index in test}) * 2 == len(test)


def test_timestamp_purge_rejects_invalid_clocks_and_shared_label_mismatch():
    decisions = [OPEN, OPEN, OPEN + timedelta(minutes=15), OPEN + timedelta(minutes=30)]
    ends = [OPEN + timedelta(minutes=15), OPEN + timedelta(minutes=30),
            OPEN + timedelta(minutes=30), OPEN + timedelta(minutes=45)]
    with pytest.raises(SeriesError, match="common label end"):
        purged_time_splits(decisions, ends, folds=2)
    with pytest.raises(SeriesError, match="timezone-aware"):
        purged_time_splits([OPEN.replace(tzinfo=None)] * 2, [OPEN] * 2, folds=2)
    with pytest.raises(SeriesError, match="precede"):
        purged_time_splits([OPEN, OPEN + timedelta(minutes=15)], [OPEN - timedelta(minutes=1),
                           OPEN + timedelta(minutes=30)], folds=2)


def test_training_noise_threshold_and_probabilities_fail_closed():
    assert noise_threshold([0.001, -0.002, 0.003]) == 0.001
    with pytest.raises(SeriesError, match="training returns"):
        noise_threshold([])
    with pytest.raises(SeriesError, match="must be positive"):
        noise_threshold([0.0, 0.0])
    with pytest.raises(SeriesError, match="sum to one"):
        check_probabilities({"UP": 0.5, "DOWN": 0.6, "FLAT": 0.0})


def test_cost_aware_flat_band_uses_training_noise_or_all_in_cost_floor():
    returns = [0.01, -0.02, 0.03]
    assert cost_aware_flat_band(returns, 0.005) == 0.01
    assert cost_aware_flat_band(returns, 0.02) == 0.02
    with pytest.raises(SeriesError, match="finite and nonnegative"):
        cost_aware_flat_band(returns, -0.001)


def test_index_forecast_hard_baselines_are_explicit_and_training_only():
    walk = random_walk_baseline(0.01)
    assert walk["method"] == "RANDOM_WALK"
    assert walk["expected_return"] == 0.0
    assert walk["predicted_label"] == "FLAT"

    no_direction = volatility_aware_no_direction_baseline([0.01, -0.02, 0.03], 0.005)
    assert no_direction["method"] == "VOLATILITY_AWARE_NO_DIRECTION"
    assert no_direction["predicted_label"] == "FLAT"
    assert no_direction["flat_band"] >= no_direction["expected_move_abs"]

    previous = previous_bar_baseline(0.04, 0.01)
    assert previous["method"] == "PREVIOUS_BAR"
    assert previous["predicted_label"] == "UP"
    assert previous["expected_return"] == 0.04
    assert previous_bar_baseline(0.005, 0.01)["predicted_label"] == "FLAT"

    climatology = same_time_of_day_baseline(
        {"open": ["UP", "UP", "FLAT"]}, ["UP", "DOWN", "FLAT", "FLAT"])
    assert climatology("open") == {"UP": 2 / 3, "DOWN": 0.0, "FLAT": 1 / 3}
    assert climatology("midday") == {"UP": 0.25, "DOWN": 0.25, "FLAT": 0.5}
    with pytest.raises(SeriesError, match="training labels"):
        same_time_of_day_baseline({}, [])


def test_session_regimes_cover_time_events_expiry_and_spread_unknowns():
    first_open = OPEN
    assert "FIRST_15_MINUTES" in session_regimes(
        first_open, macro_event=True, expiry_day=True,
        option_spread_fraction=.05, normal_spread_limit=.02)
    assert "MIDDAY" in session_regimes(
        OPEN.replace(hour=12), macro_event=False, expiry_day=False,
        option_spread_fraction=.01, normal_spread_limit=.02)
    close = OPEN.replace(hour=15, minute=10)
    regimes = session_regimes(close, macro_event=None, expiry_day=None,
                              option_spread_fraction=None, normal_spread_limit=None)
    assert {"PRE_CLOSE", "MACRO_EVENT_UNKNOWN", "EXPIRY_STATUS_UNKNOWN",
            "OPTION_SPREAD_UNKNOWN"}.issubset(regimes)
    with pytest.raises(SeriesError, match="session"):
        session_regimes(OPEN.replace(hour=8), macro_event=False, expiry_day=False,
                        option_spread_fraction=None, normal_spread_limit=None)
