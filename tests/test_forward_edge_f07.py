"""F07 - research tools without leakage (FE-0079 .. FE-0090). Synthetic data; these tests do not prove alpha."""
from __future__ import annotations

import random
from decimal import Decimal as D

import pytest

from financial_brain.forward_edge import research_tools as R


# ---- FE-0079 ----

def test_opportunities_are_sealed_before_outcomes_and_cannot_be_rewritten() -> None:
    log = R.ShadowLog()
    with pytest.raises(R.ResearchError, match="sealed"):
        log.attach_outcome("orb", "d1", "INFY", 0.01)
    log.seal("orb", "d1", "INFY", "LONG")
    log.seal("orb", "d1", "TCS", "NONE")
    with pytest.raises(R.ResearchError, match="rewritten"):
        log.seal("orb", "d1", "INFY", "SHORT")
    assert log.pending() == [("orb", "d1", "INFY"), ("orb", "d1", "TCS")]
    log.attach_outcome("orb", "d1", "INFY", 0.01)
    assert log.pending() == [("orb", "d1", "TCS")]
    with pytest.raises(R.ResearchError, match="already"):
        log.attach_outcome("orb", "d1", "INFY", 0.02)
    assert len(log.opportunities("orb")) == 2     # the no-signal opportunity is logged too


# ---- FE-0080 / FE-0090 ----

def test_horizon_and_parameters_cannot_change_after_forward_data() -> None:
    plan = R.HorizonPlan("M1", 20, True)
    R.assert_horizon_unchanged(plan, 20, forward_data_exists=True)
    R.assert_horizon_unchanged(plan, 60, forward_data_exists=False)
    with pytest.raises(R.ResearchError, match="new trial"):
        R.assert_horizon_unchanged(plan, 60, forward_data_exists=True)
    with pytest.raises(R.ResearchError):
        R.HorizonPlan("M1", 0, True)


def test_parameter_tuning_after_prospective_start_is_a_new_trial() -> None:
    frozen = {"lookback": "252", "top": "0.2"}
    R.assert_params_frozen(frozen, dict(frozen), prospective_started=True)
    R.assert_params_frozen(frozen, {"lookback": "126", "top": "0.2"}, prospective_started=False)
    with pytest.raises(R.ResearchError, match="lookback"):
        R.assert_params_frozen(frozen, {"lookback": "126", "top": "0.2"}, prospective_started=True)


# ---- FE-0081 ----

def test_net_alpha_is_portfolio_level_after_costs_financing_and_cash_flows() -> None:
    out = R.portfolio_net_alpha(equity_start=D("1000000"), gross_pnl=D("12000"), costs=D("1500"), financing=D("500"),
                                benchmark_return=D("0.004"))
    assert out["net_pnl"] == D("10000") and out["net_return"] == D("0.01") and out["net_alpha"] == D("0.006")
    with_flow = R.portfolio_net_alpha(equity_start=D("900000"), gross_pnl=D("10000"), costs=D("0"), financing=D("0"),
                                      benchmark_return=D("0"), external_cash_flow=D("100000"))
    assert with_flow["net_return"] == D("0.01")
    with pytest.raises(R.ResearchError):
        R.portfolio_net_alpha(equity_start=D("0"), gross_pnl=D("1"), costs=D("0"), financing=D("0"), benchmark_return=D("0"))


# ---- FE-0082 ----

def test_paired_comparison_isolates_value_over_the_control_not_market_direction() -> None:
    market = [0.01, -0.02, 0.015, 0.0, 0.005, -0.01]
    strat = [m + 0.002 for m in market]          # same market exposure plus a constant increment
    out = R.paired_difference(strat, market)
    assert abs(out["mean_diff"] - 0.002) < 1e-12 and out["n"] == 6.0
    with pytest.raises(R.ResearchError):
        R.paired_difference([0.1], [0.1, 0.2])


# ---- FE-0083 ----

def test_pattern_uplift_vanishes_when_a_known_factor_explains_it() -> None:
    rng = random.Random(5)
    n = 400
    momentum = [rng.gauss(0, 1) for _ in range(n)]
    pattern = [1.0 if m + rng.gauss(0, 0.3) > 0.5 else 0.0 for m in momentum]   # pattern is a noisy momentum proxy
    y = [0.01 * m + rng.gauss(0, 0.01) for m in momentum]                       # returns come from momentum only
    naive = R.incremental_uplift(y, pattern, [])
    controlled = R.incremental_uplift(y, pattern, [momentum])
    assert naive["pattern_beta"] > 0.005 and abs(naive["t"]) > 3        # looks like an edge...
    assert abs(controlled["t"]) < 2.5                                    # ...but it is already-known momentum


def test_uplift_needs_enough_observations() -> None:
    with pytest.raises(R.ResearchError):
        R.incremental_uplift([0.1, 0.2], [1, 0], [[0.1, 0.2]])


# ---- FE-0084 ----

def test_abstention_keeps_a_fixed_denominator_and_counts_missed_winners() -> None:
    out = R.abstention_accounting([("TAKE", 0.01), ("ABSTAIN", 0.03), ("ABSTAIN", -0.01), ("TAKE", -0.02)])
    assert out["total"] == 4 and out["taken"] == 2 and out["abstained"] == 2 and out["missed_winners"] == 1
    assert abs(out["mean_abstained"] - 0.01) < 1e-12


# ---- FE-0085 .. FE-0087 ----

def test_intraday_edge_is_measured_beyond_the_same_time_of_day_baseline() -> None:
    assert abs(R.time_of_day_excess([0.002, 0.003], [0.002, 0.002]) - 0.0005) < 1e-12
    with pytest.raises(R.ResearchError):
        R.time_of_day_excess([], [0.1])


def test_futures_return_pays_spread_roll_and_funding() -> None:
    assert R.futures_net_return(price_return=D("0.010"), spread_cost=D("0.001"), roll_cost=D("0.002"),
                                funding_cost=D("0.003")) == D("0.004")


def test_option_spread_without_executable_quotes_is_ineligible_even_with_a_mid_return() -> None:
    assert R.spread_net_at_bid_ask(entry_credit_at_bid=None, exit_debit_at_ask=D("5"), quoted_mid_return=D("0.2")) == \
        ("INELIGIBLE_NO_EXECUTABLE_QUOTE", None)
    assert R.spread_net_at_bid_ask(entry_credit_at_bid=D("12"), exit_debit_at_ask=D("5")) == ("OK", D("7"))


# ---- FE-0089 ----

def test_negative_inconclusive_and_no_signal_cohorts_stay_discoverable() -> None:
    a = R.CohortArchive()
    a.add({"cohort_id": "C1", "result": "NEGATIVE"})
    a.add({"cohort_id": "C2", "result": "NO_SIGNAL"})
    a.add({"cohort_id": "C3", "result": "POSITIVE"})
    assert len(a.all()) == 3
    assert [r["cohort_id"] for r in a.not_positive()] == ["C1", "C2"]
    with pytest.raises(R.ResearchError):
        a.add({"cohort_id": "C4"})
