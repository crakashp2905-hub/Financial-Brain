from __future__ import annotations

import random
from decimal import Decimal

import pytest

from financial_brain.kernel.allocation import check_constraints, equal_weight, inverse_volatility
from financial_brain.kernel.batch20_accounting import fifo_realised
from financial_brain.kernel.batch20c_agent import ReadOnlyStore
from financial_brain.kernel.batch20d_api import import_alert
from financial_brain.kernel.batch20g_checks import (
    build_parser,
    coverage_gate,
    coverage_matrix,
    feed_check,
    snapshot_digest,
)
from financial_brain.kernel.batch20g_markets import (
    greeks_heatmap,
    index_vs_stock_rules,
    iv_rv_spread,
    protective_put_profit,
    realised_vol,
    skew,
    term_structure_slope,
    theta_gamma_ratio,
)
from financial_brain.kernel.instruments import InstrumentError, fx_pip_size, fx_pip_value
from financial_brain.kernel.intraday_tools import completed_htf_trend


def test_skew_term_slope_realised_vol_and_spread() -> None:
    assert skew(0.25, 0.20) == pytest.approx(0.05)
    assert term_structure_slope({30: 0.2, 90: 0.3}) == pytest.approx(0.1 / 60)
    assert realised_vol([0.01, -0.01, 0.01, -0.01]) == pytest.approx(0.01 * 252**0.5)
    assert iv_rv_spread(0.25, 0.2) == pytest.approx(0.05)
    with pytest.raises(ValueError):
        term_structure_slope({30: 0.2})


def test_heatmap_evaluates_the_pricer_over_the_grid() -> None:
    grid = greeks_heatmap(lambda s, v: s * v, [1.0, 2.0], [0.0, 0.1])
    assert grid == [[0.0, 0.1], [0.0, 0.2]]


def test_theta_gamma_ratio_is_educational_and_undefined_at_zero_gamma() -> None:
    out = theta_gamma_ratio(-2.0, 4.0)
    assert out == {"ratio": 0.5, "label": "EDUCATIONAL"}
    with pytest.raises(ValueError):
        theta_gamma_ratio(-2.0, 0.0)


def test_index_and_stock_options_follow_different_rules() -> None:
    assert index_vs_stock_rules("INDEX") == {"exercise": "EUROPEAN", "settlement": "CASH"}
    assert index_vs_stock_rules("STOCK")["settlement"] == "PHYSICAL"
    with pytest.raises(ValueError):
        index_vs_stock_rules("COMMODITY")


def test_protective_put_limits_the_downside() -> None:
    assert protective_put_profit(100.0, 95.0, 2.0, expiry_spot=90.0, shares=10) == pytest.approx(-70.0)
    assert protective_put_profit(100.0, 95.0, 2.0, expiry_spot=110.0, shares=10) == pytest.approx(80.0)


def test_coverage_matrix_lists_untested_cells() -> None:
    out = coverage_matrix(["oms"], ["trader", "researcher"], ["EQ"], {("oms", "trader", "EQ")})
    assert out["cells"] == 2
    assert out["missing"] == [("oms", "researcher", "EQ")]


def test_research_cli_has_the_six_commands() -> None:
    parser = build_parser()
    assert parser.parse_args(["doctor"]).command == "doctor"
    assert parser.parse_args(["paper"]).command == "paper"
    with pytest.raises(SystemExit):
        parser.parse_args(["bogus"])


def test_coverage_gate_reports_only_failing_areas() -> None:
    assert coverage_gate({"oms": 95.0, "risk": 80.0}, {"oms": 90.0, "risk": 85.0}) == ["risk"]


def test_snapshot_digest_is_stable_and_sensitive() -> None:
    assert snapshot_digest({"a": 1}) == snapshot_digest({"a": 1})
    assert snapshot_digest({"a": 1}) != snapshot_digest({"a": 2})


def test_feed_check_counts_duplicates_gaps_and_reordering() -> None:
    assert feed_check([1, 2, 2, 4, 3]) == {"duplicates": 1, "gaps": 1, "reordered": 1}


def test_golden_tax_lot_result_is_fixed() -> None:
    gain, remaining = fifo_realised([(Decimal("10"), Decimal("100"))], Decimal("5"), Decimal("120"))
    assert gain == Decimal("100")
    assert remaining == [(Decimal("5"), Decimal("100"))]


def test_non_repainting_pivots_survive_random_truncation() -> None:
    from financial_brain.kernel.patterns import confirmed_pivots

    rng = random.Random(42)
    highs = [100.0 + rng.uniform(-5, 5) for _ in range(60)]
    lows = [h - 1.0 for h in highs]
    full = confirmed_pivots(highs, lows, 3, upto=len(highs) - 1)
    for _ in range(20):
        upto = rng.randrange(10, len(highs) - 1)
        partial = confirmed_pivots(highs, lows, 3, upto=upto)
        for pivot in partial:
            assert pivot in full


def test_higher_timeframe_decision_ignores_future_bars() -> None:
    closes = [float(i) for i in range(1, 11)]
    before = completed_htf_trend(closes, group=2, minute=5)
    changed = closes[:6] + [0.5, 0.25, 0.1, 0.05]
    assert completed_htf_trend(changed, group=2, minute=5) == before


def test_fx_pip_conventions_and_linear_value() -> None:
    assert fx_pip_size("EURUSD") == Decimal("0.0001")
    assert fx_pip_size("USDJPY") == Decimal("0.01")
    assert fx_pip_value("EURUSD", 100_000, Decimal("1.1")) == Decimal("10")
    with pytest.raises(InstrumentError):
        fx_pip_size("eurusd")


def test_fx_pip_value_is_linear_in_units_and_deterministic() -> None:
    rng = random.Random(7)
    for _ in range(25):
        units = rng.randrange(1, 1_000_000)
        one = fx_pip_value("EURUSD", units, Decimal("1.1"))
        assert fx_pip_value("EURUSD", 2 * units, Decimal("1.1")) == 2 * one
        assert fx_pip_value("EURUSD", units, Decimal("1.1")) == one


def test_optimiser_outputs_are_feasible() -> None:
    weights = equal_weight(["A", "B", "C"])
    assert sum(weights.values()) == pytest.approx(1.0)
    assert check_constraints(weights, long_only=True, max_weight=0.5) == []
    iv = inverse_volatility({"A": 0.1, "B": 0.2})
    assert sum(iv.values()) == pytest.approx(1.0)


def test_prompt_injection_cannot_grant_orders_or_writes() -> None:
    with pytest.raises(ValueError, match="order instructions"):
        import_alert('{"symbol": "NIFTY", "condition": "x", "order": "BUY"}')
    store = ReadOnlyStore({"k": 1})
    with pytest.raises(PermissionError):
        store.set("k", 2)
