from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

import pytest

from financial_brain.kernel.closures_p import (
    adjust_for_dividends,
    clustered_mean_se,
    covered_and_protective_demo,
    credit_vertical_limits,
    decompose_cost,
    detector_state,
    diff_versions,
    dividend_reinvestment,
    drawdown_interval,
    experiment_run,
    factor_baseline,
    fill_quality,
    fundamental_factors,
    journal_pips,
    max_loss_with_fees,
    clock_drift_breaker,
    profile_memory,
    reconcile_export,
    rolling_participation,
    roll_by_volume,
    roll_date_offset,
    tax_drag,
    time_exit,
    trailing_stop_level,
    translate_at_date,
    walk_forward_parameter_report,
)


def test_detector_distinguishes_warmup_uncertain_and_confirmed() -> None:
    assert detector_state(None, warmup=5, observed=3) == "WARMUP"
    assert detector_state(1.0, warmup=5, observed=6) == "CONFIRMED"
    assert detector_state(None, warmup=5, observed=6) == "UNCERTAIN"


def test_version_diff_lists_added_removed_changed() -> None:
    assert diff_versions({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4}) == {
        "added": ["c"],
        "removed": [],
        "changed": ["b"],
    }


def test_cluster_robust_se_matches_hand_calculation() -> None:
    assert clustered_mean_se([1.0, 2.0, 3.0, 4.0], ["x", "x", "y", "y"]) == pytest.approx(math.sqrt(8) / 4)
    with pytest.raises(ValueError):
        clustered_mean_se([1.0], ["x"])


def test_drawdown_interval_is_seeded_and_bounded() -> None:
    returns = [0.1, -0.2, 0.05, -0.03, 0.08]
    lo, hi = drawdown_interval(returns, n_boot=200, seed=1)
    assert 0.0 <= lo <= hi <= 1.0
    assert drawdown_interval(returns, n_boot=200, seed=1) == (lo, hi)


def test_fill_quality_reports_all_three_benchmarks() -> None:
    out = fill_quality(Decimal("101"), Decimal("100"), Decimal("100.5"), Decimal("100.2"), "BUY")
    assert float(out["vs_arrival_bps"]) == pytest.approx(100.0)
    assert float(out["vs_mid_bps"]) == pytest.approx((101 - 100.5) / 100.5 * 10000, rel=1e-6)
    assert set(out) == {"vs_arrival_bps", "vs_mid_bps", "vs_vwap_bps"}


def test_fx_translation_uses_the_dated_rate_only() -> None:
    assert translate_at_date(Decimal("100"), "2026-11-09", {"2026-11-09": Decimal("83")}) == Decimal("8300")
    with pytest.raises(KeyError):
        translate_at_date(Decimal("100"), "2026-11-10", {"2026-11-09": Decimal("83")})


def test_decomposition_sums_back_to_gross() -> None:
    out = decompose_cost(Decimal("100"), Decimal("5"), Decimal("1"))
    assert out["net"] == Decimal("94")
    assert out["total_costs"] == Decimal("6")
    assert out["gross"] - out["total_costs"] == out["net"]


def test_tax_drag_is_pre_minus_post() -> None:
    assert tax_drag(0.10, 0.08) == pytest.approx(0.02)


def test_clock_drift_trips_only_beyond_limit() -> None:
    assert clock_drift_breaker(1000, 1500, 400) == "TRIP"
    assert clock_drift_breaker(1000, 1100, 400) == "OK"


def test_max_loss_includes_fees() -> None:
    assert max_loss_with_fees(Decimal("3"), Decimal("0.5")) == Decimal("3.5")


def test_roll_on_volume_criterion() -> None:
    assert roll_by_volume(100, 150) == "ROLL"
    assert roll_by_volume(150, 100) == "HOLD"


def test_roll_date_skips_weekends_and_holidays() -> None:
    assert roll_date_offset(date(2026, 11, 27), 2, frozenset()) == date(2026, 11, 25)
    assert roll_date_offset(date(2026, 11, 27), 2, frozenset({date(2026, 11, 26)})) == date(2026, 11, 24)


def test_reconciliation_export_is_sorted_csv() -> None:
    assert reconcile_export({"NIFTY": 25, "ABC": -5}) == "symbol,difference\nABC,-5\nNIFTY,25"


def test_pip_journal_totals() -> None:
    assert journal_pips([10.0, -5.0, 20.0]) == {"total": 25.0, "trades": 3.0}


def test_fundamental_factors_map_to_value_quality_growth() -> None:
    assert fundamental_factors(0.05, 0.2, 0.1) == {"value": 0.05, "quality": 0.2, "growth": 0.1}


def test_dividend_adjustment_scales_prices_before_ex_date() -> None:
    adjusted = adjust_for_dividends([100.0, 100.0], {1: 1.0})
    assert adjusted[0] == pytest.approx(99.0)
    assert adjusted[1] == pytest.approx(100.0)
    with pytest.raises(ValueError):
        adjust_for_dividends([100.0, 100.0], {0: 1.0})


def test_rolling_participation_has_warmup() -> None:
    assert rolling_participation([10.0, 20.0, 30.0], 2) == [None, 15.0, 25.0]


def test_trailing_stop_and_time_exit() -> None:
    assert trailing_stop_level(110.0, 0.05) == pytest.approx(104.5)
    assert time_exit(5, 5) is True
    assert time_exit(4, 5) is False
    with pytest.raises(ValueError):
        trailing_stop_level(110.0, 1.5)


def test_memory_profile_reports_a_peak() -> None:
    result, peak = profile_memory(lambda: [0] * 100_000)
    assert len(result) == 100_000
    assert peak > 0


def test_experiment_runner_keeps_input_order() -> None:
    assert experiment_run([{"x": 1}, {"x": 2}], lambda p: p["x"] * 10, max_workers=2) == [10, 20]


def test_factor_baselines_select_the_expected_halves() -> None:
    stocks = {"A": (0.1, 0.2, 10.0), "B": (0.3, 0.1, 20.0), "C": (0.2, 0.3, 5.0)}
    out = factor_baseline(stocks, 0.5)
    assert out["low_vol"] == ["A", "C"]
    assert out["quality"] == ["C", "A"]
    assert out["value"] == ["C", "A"]


def test_covered_call_and_protective_put_demonstration() -> None:
    assert covered_and_protective_demo() == {"covered_call_at_120": 120.0, "protective_put_at_90": -70.0}


def test_credit_vertical_limits() -> None:
    assert credit_vertical_limits(short_strike=110.0, long_strike=100.0, credit=3.0) == {
        "max_profit": 3.0,
        "max_loss": 7.0,
    }
    with pytest.raises(ValueError):
        credit_vertical_limits(short_strike=110.0, long_strike=100.0, credit=10.0)


def test_dividend_reinvestment_adds_whole_units() -> None:
    assert dividend_reinvestment(100, 50.0, 1.0) == 102


def test_walk_forward_report_gives_counts_and_mode() -> None:
    assert walk_forward_parameter_report(["10", "10", "20"]) == {"counts": {"10": 2, "20": 1}, "mode": "10"}
