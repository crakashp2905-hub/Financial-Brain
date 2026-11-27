from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.execution.oms.stops import StopObservation, evaluate_stop, evaluate_stop_path

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)


def quote(*, bid=99.0, ask=100.0, quantity=10):
    return StopObservation(NOW, bid, ask, quantity, "synthetic:nse-depth")


def test_sell_stop_market_uses_bid_and_models_gap_through_stop():
    result = evaluate_stop(side="SELL", order_kind="STOP_MARKET", stop_price=100.0,
                           quantity=8, observation=quote(bid=97.5, ask=98.0))
    assert result.status == "FILLED" and result.triggered
    assert result.fill_price == 97.5  # gap through stop: fill is worse than the trigger
    assert result.fill_quantity == 8 and result.remaining_quantity == 0


def test_buy_stop_market_triggers_on_ask_and_reports_partial_depth():
    result = evaluate_stop(side="BUY", order_kind="STOP_MARKET", stop_price=101.0,
                           quantity=12, observation=quote(bid=102.0, ask=102.5, quantity=5))
    assert result.status == "PARTIAL" and result.fill_price == 102.5
    assert result.fill_quantity == 5 and result.remaining_quantity == 7


def test_stop_limit_can_trigger_without_fill_when_gap_moves_past_limit():
    result = evaluate_stop(side="SELL", order_kind="STOP_LIMIT", stop_price=99.0, limit_price=98.0,
                           quantity=3, observation=quote(bid=97.0, ask=97.5))
    assert result.status == "TRIGGERED_UNFILLED" and result.triggered
    assert result.fill_price is None and result.fill_quantity == 0
    assert result.reason == "limit_not_marketable_queue_position_unknown"


def test_stop_has_no_fill_when_depth_is_absent():
    result = evaluate_stop(side="SELL", order_kind="STOP_MARKET", stop_price=100.0,
                           quantity=2, observation=quote(bid=99.0, ask=100.0, quantity=0))
    assert result.status == "TRIGGERED_NO_LIQUIDITY" and result.fill_price is None


def test_latency_requires_later_quote_instead_of_assuming_trigger_price_fill():
    result = evaluate_stop(side="SELL", order_kind="STOP_MARKET", stop_price=100.0,
                           quantity=2, observation=quote(bid=99.0), latency_seconds=0.25)
    assert result.status == "TRIGGERED_UNFILLED"
    assert result.reason == "latency_requires_a_later_executable_quote"


def test_stop_path_uses_first_executable_quote_after_latency_and_preserves_trigger_time():
    observations = (
        quote(bid=99.0, ask=100.0, quantity=20),
        StopObservation(NOW + timedelta(milliseconds=100), 98.0, 98.5, 20, "fixture:early"),
        StopObservation(NOW + timedelta(milliseconds=250), 97.0, 97.5, 4, "fixture:eligible"),
    )
    result = evaluate_stop_path(
        side="SELL", order_kind="STOP_MARKET", stop_price=100.0, quantity=8,
        observations=observations, submitted_at=NOW - timedelta(milliseconds=1),
        latency_seconds=0.25,
    )
    assert result.status == "PARTIAL"
    assert result.triggered_at == NOW
    assert result.observed_at == NOW + timedelta(milliseconds=250)
    assert result.fill_price == 97.0
    assert result.fill_quantity == 4 and result.remaining_quantity == 4


def test_stop_limit_remains_live_after_trigger_and_can_fill_on_later_marketable_quote():
    observations = (
        quote(bid=97.0, ask=97.5),
        StopObservation(NOW + timedelta(milliseconds=10), 98.5, 99.0, 3, "fixture:rebound"),
    )
    result = evaluate_stop_path(
        side="SELL", order_kind="STOP_LIMIT", stop_price=99.0, limit_price=98.0,
        quantity=5, observations=observations, submitted_at=NOW,
    )
    assert result.status == "PARTIAL" and result.triggered_at == NOW
    assert result.observed_at == NOW + timedelta(milliseconds=10)
    assert result.fill_price == 98.5 and result.fill_quantity == 3


def test_stop_path_rejects_unordered_or_naive_observation_times():
    later = quote()
    earlier = StopObservation(NOW - timedelta(seconds=1), 99.0, 100.0, 10, "fixture:earlier")
    with pytest.raises(ValueError, match="time ordered"):
        evaluate_stop_path(side="SELL", order_kind="STOP_MARKET", stop_price=100.0,
                           quantity=1, observations=(later, earlier), submitted_at=NOW)
    with pytest.raises(ValueError, match="timezone-aware"):
        StopObservation(datetime(2026, 11, 25, 10, 0), 99.0, 100.0, 10, "fixture:naive")


def test_stop_waits_when_trigger_not_observed():
    result = evaluate_stop(side="SELL", order_kind="STOP_MARKET", stop_price=98.0,
                           quantity=2, observation=quote(bid=99.0))
    assert result.status == "WAITING" and not result.triggered


@pytest.mark.parametrize("kwargs", [
    {"side": "SELL", "order_kind": "STOP_LIMIT", "stop_price": 100.0, "limit_price": 101.0},
    {"side": "BUY", "order_kind": "STOP_LIMIT", "stop_price": 100.0, "limit_price": 99.0},
])
def test_protective_stop_limit_rejects_wrong_side_limit(kwargs):
    with pytest.raises(ValueError, match="wrong side"):
        evaluate_stop(**kwargs, quantity=2, observation=quote())

