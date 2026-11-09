from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.closures_q import (
    FROZEN_DEFAULT_WEIGHTS,
    MACRO_SCENARIOS,
    PERSONA_PRESETS,
    adjust_for_bonus,
    adjusted_performance,
    beta_neutral_check,
    factor_long_short,
    losing_streak_latch,
    multistrategy_report,
    pov_slices,
    pretrade_margin_check,
    reconcile_gate,
    retained_cash_interest,
    return_on_capital,
    stream_matches_replay,
    stress_ladder,
    vwap_slices,
    weights_fixed_before_search,
)
from financial_brain.kernel.patterns import PatternState


def test_streamed_pattern_states_match_replay() -> None:
    assert stream_matches_replay([PatternState.CONFIRMED]) is True
    assert stream_matches_replay([]) is False


def test_vwap_slices_follow_the_profile_and_sum_exactly() -> None:
    assert vwap_slices(100, [1, 1, 2]) == [25, 25, 50]
    out = vwap_slices(10, [1, 1, 1])
    assert sum(out) == 10


def test_pov_slices_take_a_share_of_each_bar_until_filled() -> None:
    assert pov_slices(50, 0.1, [100, 200, 300]) == [10, 20, 20]
    with pytest.raises(ValueError):
        pov_slices(50, 0.0, [100])


def test_reconciliation_exceptions_block_trading() -> None:
    assert reconcile_gate([]) == "CLEAR"
    assert reconcile_gate(["qty mismatch NIFTY"]) == "BLOCK"


def test_return_on_capital() -> None:
    assert return_on_capital(Decimal("50"), Decimal("1000")) == Decimal("0.05")
    with pytest.raises(ValueError):
        return_on_capital(Decimal("1"), Decimal("0"))


def test_adjusted_performance_uses_dividend_adjusted_path() -> None:
    assert adjusted_performance([100.0, 100.0], {1: 1.0}) == pytest.approx(100.0 / 99.0 - 1.0)


def test_macro_scenarios_are_named_and_read_only() -> None:
    assert set(MACRO_SCENARIOS) == {"inflation", "rates_up", "currency_shock", "market_crash"}
    with pytest.raises(TypeError):
        MACRO_SCENARIOS["inflation"]["EQ"] = 0.0  # type: ignore[index]


def test_losing_streak_latches_at_the_limit() -> None:
    assert losing_streak_latch([-1, -2, 3, -1, -1, -1], 3) is True
    assert losing_streak_latch([-1, -2, 3, -1, -1, 2], 3) is False


def test_stress_ladder_scales_with_total_weight() -> None:
    assert stress_ladder({"A": 0.6, "B": 0.4}, [-0.1]) == pytest.approx({-0.1: -0.1})


def test_factor_long_short_top_minus_bottom() -> None:
    value = factor_long_short({"A": 3, "B": 2, "C": 1, "D": 0}, {"A": 0.05, "B": 0.02, "C": -0.01, "D": -0.03}, 0.25)
    assert value == pytest.approx(0.08)


def test_every_persona_has_presets() -> None:
    assert len(PERSONA_PRESETS) == 12
    assert "paper" in PERSONA_PRESETS["options_trader"]


def test_multistrategy_report_includes_equal_weight_total() -> None:
    out = multistrategy_report({"s1": [0.1, 0.0], "s2": [0.0, 0.1]})
    assert out["per_strategy"]["s1"] == pytest.approx(0.1)
    assert out["equal_weight"] == pytest.approx(1.05 * 1.05 - 1.0)


def test_bonus_keeps_notional_unchanged() -> None:
    qty, price = adjust_for_bonus(100, 50.0, 1.0)
    assert (qty, price) == (200.0, 25.0)
    assert qty * price == pytest.approx(100 * 50.0)


def test_pretrade_margin_check_against_initial_rate() -> None:
    assert pretrade_margin_check(Decimal("100000"), Decimal("25000"), Decimal("0.2")) == "OK"
    assert pretrade_margin_check(Decimal("100000"), Decimal("25000"), Decimal("0.3")) == "INSUFFICIENT"


def test_beta_neutral_check_within_tolerance() -> None:
    assert beta_neutral_check({"A": 1.0, "B": -1.0}, {"A": 1.2, "B": 1.2}, 0.01) is True
    assert beta_neutral_check({"A": 1.0, "B": -1.0}, {"A": 1.2, "B": 1.0}, 0.01) is False


def test_retained_cash_earns_simple_interest() -> None:
    assert retained_cash_interest(Decimal("1000"), Decimal("0.05"), 365) == Decimal("50")


def test_frozen_default_weights_cannot_be_mutated_and_search_cannot_move_them() -> None:
    with pytest.raises(TypeError):
        FROZEN_DEFAULT_WEIGHTS["EQ"] = 0.9  # type: ignore[index]
    assert weights_fixed_before_search(FROZEN_DEFAULT_WEIGHTS, dict(FROZEN_DEFAULT_WEIGHTS)) is True
    assert weights_fixed_before_search({"EQ": 0.6}, {"EQ": 0.7}) is False
