from __future__ import annotations

import socket
from decimal import Decimal
from typing import Any

import contextlib
from collections.abc import Iterator


# FB-0078 runtime guard, kept in the test harness so kernel code never imports socket.
@contextlib.contextmanager
def no_network() -> Iterator[None]:
    original = socket.socket

    class _Blocked:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("network access is blocked inside this block")

    socket.socket = _Blocked  # type: ignore[misc,assignment]
    try:
        yield
    finally:
        socket.socket = original  # type: ignore[misc]



import pytest

from financial_brain.kernel.closures_r import (
    ack_dedupe,
    adx,
    after_tax_estimate,
    attribution_by,
    eval_expression,
    fill_allowed,
    funding_cost,
    iv_pnl,
    legging_action,
    oco_cancel,
    oto_child_state,
    partition_ranges,
    range_reversion_signal,
    risk_preview,
    roll_by_volume_or_oi,
    rights_ex_price,
    session_close_action,
    sharpe_interval,
    simulate_hedge,
    spend_allowed,
    spread_hedge_ratio,
    stop_limit_fill,
    te_decomposition,
    trend_signal,
    valuation_range,
)


def test_runtime_network_guard_blocks_sockets_then_restores() -> None:
    with no_network():
        with pytest.raises(RuntimeError, match="blocked"):
            socket.socket()
    s = socket.socket()
    s.close()


def test_adx_is_high_in_a_clean_uptrend() -> None:
    highs = [100.0 + i for i in range(60)]
    lows = [99.0 + i for i in range(60)]
    closes = [99.5 + i for i in range(60)]
    assert adx(highs, lows, closes, n=14) > 90.0
    with pytest.raises(ValueError):
        adx(highs[:5], lows[:5], closes[:5], n=14)


def test_expression_evaluates_named_inputs_only() -> None:
    assert eval_expression("2*(x+1)-y/2", {"x": 1.0, "y": 4.0}) == pytest.approx(2.0)
    with pytest.raises(ValueError):
        eval_expression("__import__('os')", {})


def test_partitions_are_contiguous_and_non_overlapping() -> None:
    parts = partition_ranges(100, 0.6, 0.2, 0.2)
    assert (parts["train"].start, parts["train"].stop) == (0, 60)
    assert (parts["holdout"].start, parts["holdout"].stop) == (60, 80)
    assert (parts["forward"].start, parts["forward"].stop) == (80, 100)
    with pytest.raises(ValueError):
        partition_ranges(100, 0.6, 0.3, 0.3)


def test_sharpe_interval_is_seeded() -> None:
    returns = [0.01, -0.005, 0.02, 0.0, 0.015, -0.01]
    assert sharpe_interval(returns, n_boot=200, seed=2) == sharpe_interval(returns, n_boot=200, seed=2)


def test_stop_limit_fills_only_after_trigger_and_limit_reach() -> None:
    assert stop_limit_fill("BUY", stop=101.0, limit=101.5, high=102.0, low=100.8) == 101.5
    assert stop_limit_fill("BUY", stop=101.0, limit=101.5, high=102.0, low=101.6) is None
    assert stop_limit_fill("BUY", stop=101.0, limit=101.5, high=100.5, low=100.0) is None


def test_no_fill_on_zero_volume_or_auction() -> None:
    assert fill_allowed(0, "CONTINUOUS") is False
    assert fill_allowed(100, "AUCTION") is False
    assert fill_allowed(100, "CONTINUOUS") is True


def test_legging_action_for_each_fill_pattern() -> None:
    assert legging_action({"A": True, "B": True}) == "COMPLETE"
    assert legging_action({"A": False, "B": False}) == "CANCEL_ALL"
    assert legging_action({"A": True, "B": False}) == "UNWIND_FILLED"


def test_duplicate_acks_are_dropped() -> None:
    assert ack_dedupe(["a1", "a2", "a1"]) == (["a1", "a2"], 1)


def test_oco_and_oto_dependencies() -> None:
    assert oco_cancel("target", ["stop", "target"]) == ["stop"]
    with pytest.raises(ValueError):
        oco_cancel("x", ["stop", "target"])
    assert oto_child_state(parent_filled=False) == "PENDING"
    assert oto_child_state(parent_filled=True) == "ACTIVE"


def test_after_tax_taxes_gains_only_and_is_labelled() -> None:
    out = after_tax_estimate(Decimal("1000"), Decimal("0.2"))
    assert out["after_tax"] == Decimal("800") and out["label"] == "ESTIMATE"
    assert after_tax_estimate(Decimal("-100"), Decimal("0.2"))["tax"] == Decimal("0")


def test_restricted_mode_refuses_spend() -> None:
    assert spend_allowed("RESTRICTED", 10.0, 100.0) is False
    assert spend_allowed("NORMAL", 50.0, 100.0) is True
    assert spend_allowed("NORMAL", 150.0, 100.0) is False


def test_attribution_sums_by_key() -> None:
    assert attribution_by([("s1", 10.0), ("s2", -5.0), ("s1", 5.0)]) == {"s1": 15.0, "s2": -5.0}


def test_tracking_error_splits_into_beta_and_residual() -> None:
    bench = [0.01, -0.02, 0.03, 0.0]
    out = te_decomposition([2 * b for b in bench], bench)
    assert out["beta"] == pytest.approx(2.0)
    assert out["residual_te"] == pytest.approx(0.0, abs=1e-12)


def test_risk_preview_shows_volatility_change() -> None:
    cov = [[0.04, 0.0], [0.0, 0.09]]
    out = risk_preview(cov, [0.5, 0.5], [0.6, 0.4])
    assert out["vol_before"] == pytest.approx(0.1803, abs=1e-4)
    assert out["vol_after"] == pytest.approx(0.1697, abs=1e-4)
    assert out["change"] < 0


def test_hedge_path_pnl_uses_delta_over_each_step() -> None:
    assert simulate_hedge([100.0, 102.0, 101.0], [1.0, -1.0]) == pytest.approx(3.0)
    with pytest.raises(ValueError):
        simulate_hedge([100.0, 102.0], [1.0, 1.0])


def test_vega_pnl_on_an_iv_drop() -> None:
    assert iv_pnl(100.0, -0.02) == pytest.approx(-2.0)


def test_roll_on_either_volume_or_open_interest() -> None:
    assert roll_by_volume_or_oi(100, 150, 200, 100) == "ROLL"
    assert roll_by_volume_or_oi(100, 50, 200, 100) == "HOLD"
    assert roll_by_volume_or_oi(100, 50, 100, 150) == "ROLL"


def test_spread_hedge_ratio_by_notional() -> None:
    assert spread_hedge_ratio(100.0, 50.0, 1.0, 2.0) == pytest.approx(1.0)


def test_session_close_flattens_inside_cutoff() -> None:
    assert session_close_action(900, 930, 30) == "FLATTEN"
    assert session_close_action(800, 930, 30) == "HOLD"


def test_range_reversion_buys_low_and_sells_high() -> None:
    assert range_reversion_signal(99.0, 100.0, 110.0) == 1
    assert range_reversion_signal(111.0, 100.0, 110.0) == -1
    assert range_reversion_signal(105.0, 100.0, 110.0) == 0


def test_trend_signal_compares_close_with_window_mean() -> None:
    assert trend_signal([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 3) == 1
    assert trend_signal([6.0, 5.0, 4.0, 3.0, 2.0, 1.0], 3) == -1


def test_funding_cost_scales_with_days() -> None:
    assert float(funding_cost(Decimal("100000"), Decimal("0.08"), 30)) == pytest.approx(657.534, rel=1e-4)


def test_valuation_range_gives_min_max_and_mid() -> None:
    out = valuation_range({"bear": Decimal("10"), "base": Decimal("15"), "bull": Decimal("20")})
    assert out == {"min": Decimal("10"), "max": Decimal("20"), "mid": Decimal("15")}


def test_rights_issue_ex_price() -> None:
    out = rights_ex_price(Decimal("100"), 10, Decimal("80"), 2)
    assert out == (Decimal("1000") + Decimal("160")) / Decimal("12")
