from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.batch20_accounting import (
    consolidated_total,
    contribution_path,
    dividend_postings,
    excess_pnl,
    fifo_realised,
    option_exercise_flows,
    tax_estimate,
)
from financial_brain.kernel.batch20_ops import (
    JobQueue,
    ancestors,
    equal_weight_series,
    sweep,
    time_block,
    validate_scenario,
)
from financial_brain.kernel.batch20_portfolio import (
    dollar_neutral,
    is_psd_2x2,
    min_variance_weight,
    policy_template,
    shrink_covariance,
    total_return,
    trade_proposals,
    tracking_error,
)


def test_dividend_postings_balance_to_gross() -> None:
    out = dividend_postings(Decimal("100"), Decimal("2"), Decimal("0.1"))
    assert out == {"dividend_income": Decimal("200"), "cash": Decimal("180"), "withholding": Decimal("20")}


def test_option_exercise_and_assignment_flows_have_opposite_signs() -> None:
    long_ex = option_exercise_flows("LONG_EXERCISE", 2, Decimal("100"), Decimal("50"))
    assert long_ex == {"cash": Decimal("-10000"), "shares": Decimal("100")}
    short_as = option_exercise_flows("SHORT_ASSIGNED", 2, Decimal("100"), Decimal("50"))
    assert short_as == {"cash": Decimal("10000"), "shares": Decimal("-100")}


def test_consolidated_total_refuses_missing_rates() -> None:
    balances = {"A": {"USD": Decimal("100"), "INR": Decimal("8000")}, "B": {"USD": Decimal("50")}}
    rates = {"USD": Decimal("1"), "INR": Decimal("0.012")}
    assert consolidated_total(balances, rates) == Decimal("246")
    with pytest.raises(KeyError):
        consolidated_total(balances, {"USD": Decimal("1")})


def test_excess_pnl_and_tax_estimate_label() -> None:
    assert excess_pnl(0.05, 0.02) == pytest.approx(0.03)
    est = tax_estimate(Decimal("1000"), "IN")
    assert est == {"tax": Decimal("150"), "label": "ESTIMATE"}
    assert tax_estimate(Decimal("-500"), "US")["tax"] == Decimal("0")
    with pytest.raises(KeyError):
        tax_estimate(Decimal("1"), "XX")


def test_fifo_consumes_oldest_lots_first() -> None:
    gain, remaining = fifo_realised([(Decimal("10"), Decimal("100")), (Decimal("10"), Decimal("120"))], Decimal("15"), Decimal("130"))
    assert gain == Decimal("350")
    assert remaining == [(Decimal("5"), Decimal("120"))]


def test_contribution_path_and_withdrawal_floor() -> None:
    assert contribution_path(Decimal("0"), Decimal("100"), Decimal("0"), Decimal("0"), 3) == Decimal("300")
    with pytest.raises(ValueError, match="exceed"):
        contribution_path(Decimal("0"), Decimal("0"), Decimal("10"), Decimal("0"), 1)


def test_min_variance_weight_and_clipping() -> None:
    assert min_variance_weight(0.04, 0.09, 0.0) == pytest.approx(0.6923, abs=1e-3)
    with pytest.raises(ValueError, match="degenerate"):
        min_variance_weight(0.04, 0.09, 0.2)  # denominator is negative
    assert min_variance_weight(0.04, 0.01, 0.02) == pytest.approx(0.0)  # raw weight -1 clipped to 0
    assert min_variance_weight(0.01, 0.04, 0.02) == pytest.approx(1.0)  # raw weight 2 clipped to 1


def test_shrinkage_pulls_off_diagonal_toward_zero() -> None:
    shrunk = shrink_covariance([[1.0, 0.5], [0.5, 1.0]], 0.5)
    assert shrunk == [[1.0, 0.25], [0.25, 1.0]]
    assert is_psd_2x2(shrunk) is True
    assert is_psd_2x2([[1.0, 2.0], [2.0, 1.0]]) is False


def test_policy_templates_by_horizon() -> None:
    assert policy_template("medium")["max_weight"] == Decimal("0.35")
    with pytest.raises(KeyError):
        policy_template("intraday")


def test_dollar_neutral_within_tolerance() -> None:
    assert dollar_neutral(Decimal("1000"), Decimal("-1000"), Decimal("0")) is True
    assert dollar_neutral(Decimal("1000"), Decimal("-900"), Decimal("50")) is False
    assert dollar_neutral(Decimal("1000"), Decimal("-900"), Decimal("100")) is True


def test_trade_proposals_rank_by_turnover() -> None:
    ranked = trade_proposals({"A": 0.6, "B": 0.4}, {"keep": {"A": 0.6, "B": 0.4}, "tilt": {"A": 0.5, "B": 0.5}})
    assert ranked[0] == ("keep", 0.0)
    assert ranked[1] == ("tilt", pytest.approx(0.1))


def test_tracking_error_and_total_return() -> None:
    assert tracking_error([0.01, 0.02], [0.0, 0.0]) == pytest.approx(0.005)
    assert total_return(Decimal("100"), Decimal("102"), Decimal("3")) == Decimal("0.05")


def test_time_block_returns_result_and_elapsed() -> None:
    result, elapsed = time_block(lambda: 42)
    assert result == 42 and elapsed >= 0.0


def test_sweep_respects_budget() -> None:
    combos = sweep({"a": [1, 2], "b": ["x", "y", "z"]}, budget=6)
    assert len(combos) == 6
    with pytest.raises(ValueError, match="exceeds budget"):
        sweep({"a": [1, 2], "b": ["x", "y", "z"]}, budget=5)


def test_scenario_validation() -> None:
    validate_scenario("crash", {"EQ": -0.3}, existing=set())
    with pytest.raises(ValueError, match="already exists"):
        validate_scenario("crash", {"EQ": -0.3}, existing={"crash"})
    with pytest.raises(ValueError, match=r"\[-1, 1\]"):
        validate_scenario("bad", {"EQ": 1.5}, existing=set())


def test_equal_weight_series_averages_per_period() -> None:
    assert equal_weight_series({"s1": [0.02, 0.00], "s2": [0.00, 0.02]}) == pytest.approx([0.01, 0.01])
    with pytest.raises(ValueError):
        equal_weight_series({"s1": [0.1], "s2": [0.1, 0.2]})


def test_job_queue_never_exceeds_the_cap() -> None:
    q = JobQueue(max_running=1)
    q.submit("j1")
    q.submit("j2")
    assert q.start_next() == "j1"
    assert q.start_next() is None
    q.finish("j1")
    assert q.start_next() == "j2"


def test_ancestors_walks_lineage_and_refuses_cycles() -> None:
    assert ancestors({"c": ["b"], "b": ["a"]}, "c") == {"a", "b"}
    with pytest.raises(ValueError, match="cycle"):
        ancestors({"x": ["y"], "y": ["x"]}, "x")
