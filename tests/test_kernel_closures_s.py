from __future__ import annotations

import random
from decimal import Decimal

import pytest

from financial_brain.kernel.baselines import ma_crossover
from financial_brain.kernel.closures_s import (
    adjust_position_cost,
    base_currency_pnl,
    carry_components,
    dividend_rebalance,
    evidence_report,
    fx_signals,
    hedge_drift,
    hedged_basket_notional,
    journal_pips_money,
    lot_tax_drag,
    max_loss_with_settlement,
    max_pairwise_correlation,
    paper_daily_pnl,
    portfolio_short_margin,
    regime_metrics,
    residual_report,
    returns_by_year,
    run_replay,
    spread_zscore_signal,
    stream_ma_crossover,
    stress_ladder_by_asset,
    technical_pass,
    thesis_exit,
)
from financial_brain.kernel.intraday_tools import ReplayControl


def test_returns_grouped_by_year() -> None:
    assert returns_by_year([(2025, 0.1), (2025, 0.3), (2026, -0.1)]) == {2025: pytest.approx(0.2), 2026: -0.1}


def test_lot_tax_drag_taxes_the_realised_gain_only() -> None:
    out = lot_tax_drag([(Decimal("10"), Decimal("100"))], Decimal("5"), Decimal("120"), Decimal("0.2"))
    assert out == {"gain": Decimal("100"), "tax": Decimal("20"), "after_tax_gain": Decimal("80")}


def test_stress_ladder_uses_per_asset_shocks() -> None:
    assert stress_ladder_by_asset({"EQ": 0.6, "BOND": 0.4}, {-0.1: {"EQ": -0.2, "BOND": 0.0}}) == pytest.approx(
        {-0.1: -0.12}
    )


def test_settlement_cost_adds_to_max_loss() -> None:
    assert max_loss_with_settlement(Decimal("3"), Decimal("0.5"), Decimal("0.25")) == Decimal("3.75")


def test_portfolio_margin_counts_only_short_positions() -> None:
    positions = [
        (Decimal("100"), Decimal("110"), Decimal("2"), "CALL", -1),
        (Decimal("100"), Decimal("90"), Decimal("2"), "PUT", 2),
    ]
    assert portfolio_short_margin(positions) == Decimal("12")


def test_position_adjustment_cost_is_premium_change_times_quantity() -> None:
    assert adjust_position_cost(10, Decimal("2"), Decimal("2.5")) == Decimal("5")


def test_paper_options_daily_pnl_from_marks() -> None:
    assert paper_daily_pnl({"A": 2}, [{"A": 100.0}, {"A": 103.0}, {"A": 101.0}]) == pytest.approx([6.0, -4.0])


def test_evidence_report_counts_and_tail_per_regime() -> None:
    out = evidence_report([0.1, -0.1, 0.2, -0.2], ["up", "down", "up", "down"], alpha=0.5)
    assert out["counts"] == {"down": 2, "up": 2}
    assert set(out["tail_loss"]) == {"up", "down"}  # type: ignore[arg-type]


def test_carry_components_sum_to_the_basis() -> None:
    out = carry_components(Decimal("101"), Decimal("100"), Decimal("0.05"), Decimal("0.01"), 365, Decimal("0"))
    assert out["basis"] == Decimal("1")
    assert out["carry"] == Decimal("4")
    assert out["basis"] == out["carry"] + out["residual"]


def test_strongest_pair_is_the_perfectly_correlated_one() -> None:
    pair = max_pairwise_correlation({"A": [1.0, 2.0, 3.0], "B": [2.0, 4.0, 6.0], "C": [3.0, 1.0, 2.0]})
    assert (pair[0], pair[1]) == ("A", "B")
    assert pair[2] == pytest.approx(1.0)


def test_hedge_drift_against_target_ratio() -> None:
    assert hedge_drift(0.5, 240.0, 500.0) == pytest.approx(-0.02)


def test_base_currency_conversion() -> None:
    assert base_currency_pnl(Decimal("10"), Decimal("0.9")) == Decimal("9")


def test_pip_journal_in_pips_and_money() -> None:
    assert journal_pips_money([(10.0, 100.0), (-5.0, 100.0)]) == {"pips": 5.0, "money": 500.0}


def test_technical_screen_passes_a_close_above_its_window_mean() -> None:
    assert technical_pass([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 3) is True
    assert technical_pass([6.0, 5.0, 4.0, 3.0, 2.0, 1.0], 3) is False


def test_residual_report_is_zero_for_a_scaled_index() -> None:
    out = residual_report([2.0, 4.0, 6.0], [1.0, 2.0, 3.0])
    assert out["beta"] == pytest.approx(2.0)  # type: ignore[arg-type]
    assert all(abs(r) < 1e-9 for r in out["residuals"])  # type: ignore[union-attr]


def test_thesis_exit_on_invalidation_or_factor_decay() -> None:
    assert thesis_exit(95.0, 94.0, [1.0, 1.0], 0.3, 2) is True
    assert thesis_exit(95.0, 100.0, [1.0, 1.0, 1.0], 0.3, 2) is False


def test_replay_driver_respects_pause_and_step_limit() -> None:
    assert run_replay([1, 2, 3], ReplayControl(), max_steps=2) == [1, 2]
    assert run_replay([1, 2, 3], ReplayControl(paused=True), max_steps=5) == []


def test_regime_metrics_mean_and_volatility() -> None:
    out = regime_metrics({"up": [0.01, 0.03], "down": [-0.02, -0.04]})
    assert out["up"]["mean"] == pytest.approx(0.02)
    assert out["down"]["mean"] == pytest.approx(-0.03)


def test_streamed_crossover_matches_batch_crossover() -> None:
    rng = random.Random(3)
    closes = [100.0 + rng.uniform(-5.0, 5.0) for _ in range(120)]
    assert stream_ma_crossover(closes, fast=3, slow=6) == ma_crossover(closes, 3, 6)


def test_hedged_basket_is_beta_weighted() -> None:
    assert hedged_basket_notional({"A": 0.5, "B": 0.5}, {"A": 1.0, "B": 2.0}, 1000.0) == pytest.approx(-1500.0)


def test_spread_zscore_signal_applies_from_next_bar() -> None:
    near = [10.0] * 6
    far = [10.0, 11.0, 9.0, 10.0, 4.0, 10.0]
    signal = spread_zscore_signal(near, far, window=4, entry_z=1.0)
    assert signal[4] == 0
    assert signal[5] == 1


def test_fx_signals_combine_trend_and_session_reversion() -> None:
    out = fx_signals([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], window=3, session_mean=5.0, deviation=0.05)
    assert out == {"trend": 1, "session_reversion": -1}


def test_dividend_reinvestment_then_rebalance() -> None:
    out = dividend_rebalance(
        100, 50.0, 1.0, {"A": 10}, {"A": 0.5, "B": 0.5}, {"A": 100.0, "B": 50.0}, 10000.0
    )
    assert out["units_after_dividend"] == 102
    assert out["trades"] == {"A": 40, "B": 100}
