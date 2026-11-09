from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.closures_m import (
    benchmark_granularities,
    bollinger,
    combine_signals,
    dispatch_timers,
    is_expired,
    rule_report,
    stamp_duty,
)
from financial_brain.kernel.strategy_state import StrategyState


def test_bollinger_bands_have_warmup_and_correct_width() -> None:
    bands = bollinger([1.0, 2.0, 3.0], window=3, k=1.0)
    assert bands[0] is None and bands[1] is None
    mid, upper, lower = bands[2]  # type: ignore[misc]
    assert mid == pytest.approx(2.0)
    assert upper == pytest.approx(2.0 + (2 / 3) ** 0.5)
    assert lower == pytest.approx(2.0 - (2 / 3) ** 0.5)


def test_signals_combine_with_and_or() -> None:
    assert combine_signals([1, 0, 1], [1, 1, 0], "AND") == [1, 0, 0]
    assert combine_signals([1, 0, 1], [1, 1, 0], "OR") == [1, 1, 1]
    with pytest.raises(ValueError):
        combine_signals([1], [1], "XOR")


def test_rule_report_splits_pass_and_fail() -> None:
    report = rule_report({"NIFTY": [], "BAR": ["halted"]})
    assert report == {"pass": ["NIFTY"], "fail": {"BAR": ["halted"]}}


def test_timers_fire_in_order_and_skip_unscheduled_times() -> None:
    out = dispatch_timers([2, 1, 3], {1: lambda: "open", 2: lambda: "mid"})
    assert out == ["open", "mid"]


def test_granularity_benchmark_counts_events_kept() -> None:
    events = [(1, "QUOTE"), (2, "TRADE"), (3, "BAR")]
    assert benchmark_granularities(events, ["BAR", "QUOTE", "TICK"]) == {"BAR": 1, "QUOTE": 1, "TICK": 2}


def test_each_strategy_instance_keeps_its_own_state() -> None:
    a = StrategyState(cooldown_bars=0, max_concurrent=1)
    b = StrategyState(cooldown_bars=0, max_concurrent=1)
    assert a.trigger("t1", "X", 0) is True
    assert b.trigger("t1", "X", 0) is True
    assert a.trigger("t2", "Y", 0) is False


def test_time_in_force_expiry_rules() -> None:
    assert is_expired("DAY", placed_day=1, today=2) is True
    assert is_expired("DAY", placed_day=1, today=1) is False
    assert is_expired("GTC", placed_day=1, today=999) is False
    assert is_expired("GTD", placed_day=1, today=5, expiry_day=4) is True
    with pytest.raises(ValueError):
        is_expired("GTD", placed_day=1, today=5)


def test_stamp_duty_is_proportional_to_notional() -> None:
    assert stamp_duty(Decimal("100000"), Decimal("0.00015")) == Decimal("15")
    with pytest.raises(ValueError):
        stamp_duty(Decimal("1"), Decimal("-0.1"))
