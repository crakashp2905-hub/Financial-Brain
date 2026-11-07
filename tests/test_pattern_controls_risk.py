"""Controls and risk rules (CP-0147, CP-0175, CP-0176, CP-0178, CP-0179, CP-0181, CP-0182, CP-0186..0188,
CP-0206, CP-0208..0215)."""

from __future__ import annotations

import pytest

from financial_brain.patterns.paper import (
    atr_size,
    check_intent_codes,
    correlated_exposure_ok,
    loss_if_gap,
    participation_ok,
    risk_adjusted_utility,
    risk_display,
    sector_hhi,
    stress_action,
)
from financial_brain.patterns.study import (
    ablation_variants,
    decompose,
    matched_baseline,
    neutralize_exposure,
    pattern_vs_no_trade,
    shuffled_direction_control,
    small_sample_label,
    split_selected_rejected,
    turnover,
)


def test_small_samples_are_labelled_low_confidence() -> None:
    assert small_sample_label(5) == "LOW_CONFIDENCE"
    assert small_sample_label(120) == "SAMPLE_OK"
    with pytest.raises(ValueError):
        small_sample_label(-1)


def test_direction_shuffle_preserves_long_short_counts() -> None:
    signs = [1, 1, -1, 1, -1, -1, 1]
    shuffled = shuffled_direction_control(signs, seed=3)
    assert sorted(shuffled) == sorted(signs)
    assert shuffled == shuffled_direction_control(signs, seed=3)
    with pytest.raises(ValueError):
        shuffled_direction_control([0, 1])


def test_matched_baseline_is_drawn_from_the_same_candidates() -> None:
    assert matched_baseline([3, 1], [1, 2, 3]) == [1, 3]
    with pytest.raises(ValueError):
        matched_baseline([9], [1, 2])


def test_residuals_are_uncorrelated_with_the_market() -> None:
    market = [0.01 * ((i * 7) % 11 - 5) for i in range(60)]
    returns = [0.5 * m + 0.002 * ((i * 3) % 5 - 2) for i, m in enumerate(market)]
    residuals, beta = neutralize_exposure(returns, market)
    assert beta == pytest.approx(0.5, abs=0.05)
    mean_r = sum(residuals) / len(residuals)
    cov = sum((r - mean_r) * (m - sum(market) / len(market)) for r, m in zip(residuals, market, strict=True))
    assert abs(cov) < 1e-9


def test_ablation_count_is_two_to_the_toggles() -> None:
    variants = ablation_variants(("geometry", "volume", "confirmation"))
    assert len(variants) == 8
    assert {"geometry": True, "volume": False, "confirmation": True} in variants
    with pytest.raises(ValueError):
        ablation_variants(("a", "a"))


def test_turnover_and_no_trade_baseline() -> None:
    assert turnover({"A": 0.5, "B": 0.5}, {"A": 0.5, "C": 0.5}) == pytest.approx(0.5)
    summary = pattern_vs_no_trade([0.01, -0.02])
    assert summary["net_vs_cash"] == pytest.approx(-0.01)


def test_pre_and_post_filter_breakdown_and_opportunity_cost() -> None:
    out = split_selected_rejected([0.02, -0.01, 0.03], [True, False, True])
    assert out["kept_mean"] == pytest.approx(0.025)
    assert out["opportunity_cost"] == pytest.approx(-0.01)


def test_attribution_must_reconcile_without_double_counting() -> None:
    assert decompose(0.10, entry=0.01, exit_=0.02, sizing=0.03, pattern=0.04)["total"] == 0.10
    with pytest.raises(ValueError):
        decompose(0.10, entry=0.01, exit_=0.02, sizing=0.03, pattern=0.05)


def test_intent_no_trade_codes_are_machine_readable() -> None:
    codes = check_intent_codes(stale_bars=True, ambiguous_geometry=False, low_liquidity=True,
                               insufficient_evidence=True, missing_calibration=True)
    assert codes == ["STALE_BARS", "LOW_LIQUIDITY", "INSUFFICIENT_EVIDENCE", "MISSING_CALIBRATION"]


def test_stop_is_never_presented_as_a_loss_cap() -> None:
    view = risk_display(daily_vol=0.02, gap_risk=0.05, max_adverse_excursion=0.08)
    assert view["stop_caps_loss"] is False


def test_gap_through_stop_can_exceed_nominal_loss() -> None:
    result = loss_if_gap(entry=100.0, stop=95.0, open_price=90.0, quantity=10)
    assert result["nominal_loss"] == pytest.approx(50.0)
    assert result["actual_loss"] == pytest.approx(100.0)
    assert result["exceeds_nominal"] == 1.0
    calm = loss_if_gap(entry=100.0, stop=95.0, open_price=99.0, quantity=10)
    assert calm["exceeds_nominal"] == 0.0
    with pytest.raises(ValueError):
        loss_if_gap(entry=100.0, stop=101.0, open_price=99.0, quantity=1)


def test_adv_participation_and_unknown_adv_refuse() -> None:
    assert participation_ok(100, 10_000) is True
    assert participation_ok(1_000, 10_000) is False
    assert participation_ok(10, 0) is False


def test_correlated_shapes_do_not_multiply_exposure() -> None:
    assert correlated_exposure_ok(1) is True
    assert correlated_exposure_ok(5) is False


def test_predeclared_stress_degrades_to_cash() -> None:
    assert stress_action("shock") == "CASH_NO_TRADE"
    assert stress_action("calm") == "NORMAL"


def test_sizing_uses_volatility_not_success_rate() -> None:
    assert atr_size(risk_budget=10_000, atr_value=5.0, k=2.0) == 1000
    with pytest.raises(ValueError):
        atr_size(risk_budget=0, atr_value=5.0)


def test_risk_utility_is_labelled_not_profit_and_hhi_bounds() -> None:
    out = risk_adjusted_utility(0.05, 0.02)
    assert out["label"] == "UTILITY_NOT_PROFIT"
    assert sector_hhi({"IT": 1.0}) == pytest.approx(1.0)
    assert sector_hhi({"IT": 1, "BANK": 1}) == pytest.approx(0.5)


def test_synthetic_helpful_and_harmful_overlays_are_told_apart() -> None:
    """SYNTHETIC: an overlay that removes losers helps; one that removes winners hurts (CP-0185)."""
    from financial_brain.patterns.study import incremental_value

    base = [0.02, -0.03, 0.01, -0.02, 0.03, -0.01]
    helpful = [r for r in base if r > 0] + [0.0, 0.0, 0.0]  # drops losing trades (zeros stand in for skipped days)
    harmful = [r for r in base if r < 0] + [0.0, 0.0, 0.0]  # keeps only losers and skips the winners
    assert incremental_value(helpful, base) > 0
    assert incremental_value(harmful, base) < 0
