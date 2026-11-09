from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.strategy_primitives import (
    IntentDeclaration,
    breakout,
    crossover,
    greater,
    momentum_template,
    pattern_breakout_template,
    spread,
    zscore_prior,
)


def test_missing_values_never_trigger_comparisons() -> None:
    assert greater([1.0, None], [0.5, 0.5]) == [True, None]
    assert crossover([1.0, 2.0], [None, 1.5]) == [False, False]


def test_crossover_fires_only_on_the_bar_it_crosses() -> None:
    a = [1.0, 1.0, 2.0, 3.0]
    b = [1.5, 1.5, 1.5, 1.5]
    assert crossover(a, b) == [False, False, True, False]


def test_breakout_excludes_the_current_bar() -> None:
    high = [5.0, 5.0, 5.0, 9.0]
    close = [4.0, 4.0, 4.0, 9.5]
    assert breakout(close, high, 3) == [False, False, False, True]
    assert breakout([4.0, 4.0, 4.0, 4.0], [5.0, 5.0, 5.0, 9.0], 3)[3] is False


def test_zscore_uses_prior_window_only() -> None:
    x = [1.0, 2.0, 3.0, 100.0]
    z = zscore_prior(x, 3)
    assert z[:3] == [None, None, None]
    assert z[3] is not None and z[3] > 3


def test_spread_is_aligned_and_missing_safe() -> None:
    assert spread([10.0, None], [4.0, 3.0], 2.0) == [2.0, None]
    with pytest.raises(ValueError):
        spread([1.0], [1.0, 2.0], 1.0)


def test_templates_apply_signal_from_next_bar() -> None:
    close = [1.0, 2.0, 3.0, 4.0]
    assert momentum_template(close, 1) == [False, False, True, True]
    assert pattern_breakout_template([1, 1, 1, 9], [2, 2, 2, 2], 2)[3] is False


def test_intent_bracket_directions_are_enforced() -> None:
    ok = IntentDeclaration("s", "X", "BUY", 10, entry=Decimal("100"), stop=Decimal("95"), target=Decimal("110"))
    assert ok.to_oms_intent()["bracket"] == {"stop": Decimal("95"), "target": Decimal("110")}
    with pytest.raises(ValueError, match="BUY stop"):
        IntentDeclaration("s", "X", "BUY", 10, entry=Decimal("100"), stop=Decimal("101"))
    with pytest.raises(ValueError, match="SELL target"):
        IntentDeclaration("s", "X", "SELL", 10, entry=Decimal("100"), target=Decimal("101"))


def test_time_in_force_and_trailing_are_validated() -> None:
    with pytest.raises(ValueError, match="time_in_force"):
        IntentDeclaration("s", "X", "BUY", 1, time_in_force="MOC")
    with pytest.raises(ValueError, match="trailing"):
        IntentDeclaration("s", "X", "BUY", 1, trailing_pct=Decimal("1.5"))
    assert IntentDeclaration("s", "X", "BUY", 1, time_in_force="IOC").to_oms_intent()["time_in_force"] == "IOC"


def test_bracket_requires_entry() -> None:
    with pytest.raises(ValueError, match="entry"):
        IntentDeclaration("s", "X", "BUY", 1, stop=Decimal("9"))
