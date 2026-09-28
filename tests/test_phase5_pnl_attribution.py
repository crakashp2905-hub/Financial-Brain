"""P&L attribution, which is only worth anything if it closes.

The one property that makes an attribution trustworthy is that its components sum to the thing they
are attributing. Most of this file is that identity, plus the two places an attribution lies
comfortably: a component quietly absorbing the multi-period compounding gap, and a concentration
figure that hides a run having been three bets.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from financial_brain.attribution import pnl
from financial_brain.paper import engine
from tests.test_phase4_paper_engine import START, _db


def _run(con, **kw):
    base = dict(feature="dist_52w_high", start=START, end=START + timedelta(days=119),
                rebalance=20, max_positions=3, simulate_fills=False)
    base.update(kw)
    return engine.run(con, **base)


# ------------------------------------------------------------------------- the identity
def test_the_log_decomposition_closes_to_floating_point():
    """Not "approximately closes". Either the components rebuild the compounded return or the
    decomposition is an opinion with a residual attached."""
    con = _db(names=6, sessions=120, drift=0.004)
    d = pnl.decompose(con, _run(con), min_adv=0.0)
    assert d["closes"] is True, f"residual {d['residual']:.3e}"
    assert abs(d["residual"]) < 1e-9
    assert d["log_total"] == pytest.approx(sum(d["log"].values()), abs=1e-12)


def test_the_four_components_are_market_universe_stock_and_costs():
    con = _db(names=6, sessions=120, drift=0.004)
    d = pnl.decompose(con, _run(con), min_adv=0.0)
    assert set(d["log"]) == {"market", "universe_selection", "stock_selection", "costs"}
    assert d["sessions_used"] > 0


def test_the_compounding_gap_is_reported_rather_than_absorbed_into_a_component():
    """Summing daily arithmetic contributions does not reproduce a compounded return. Every
    arithmetic attribution either smooths that gap over the components or shows it; showing it is the
    only version a reader can check."""
    con = _db(names=6, sessions=120, drift=0.01)
    d = pnl.decompose(con, _run(con), min_adv=0.0)
    assert "compounding_interaction" in d
    assert d["compounding_interaction"] == pytest.approx(
        d["compounded_total"] - d["arithmetic_total"], abs=1e-12)
    assert d["arithmetic_total"] == pytest.approx(sum(d["arithmetic"].values()), abs=1e-12)


def test_costs_appear_as_a_negative_contribution_and_vanish_when_not_charged():
    con = _db(names=6, sessions=120, drift=0.004)
    charged = pnl.decompose(con, _run(con), min_adv=0.0)
    free = pnl.decompose(con, _run(con, charge_costs=False), min_adv=0.0)
    assert charged["log"]["costs"] < 0
    assert free["log"]["costs"] == pytest.approx(0.0, abs=1e-12)
    assert charged["log"]["costs"] < free["log"]["costs"]


def test_a_universe_of_the_wrong_size_is_refused_rather_than_attributed():
    """A universe term measured against a different universe is not an attribution, and the number it
    produces looks entirely reasonable."""
    con = _db(names=6, sessions=120, drift=0.004)
    keep = {"L0", "L1", "L2"}
    run = _run(con, eligible_lineages=keep)
    with pytest.raises(pnl.AttributionError, match="not an attribution"):
        pnl.decompose(con, run, min_adv=0.0, eligible={"L0", "L1"})

    ok = pnl.decompose(con, run, min_adv=0.0, eligible=keep)
    assert ok["closes"] is True
    assert "own eligible set" in ok["universe_measured_on"]


def test_passing_no_universe_says_which_one_was_measured():
    con = _db(names=6, sessions=120, drift=0.004)
    d = pnl.decompose(con, _run(con), min_adv=0.0)
    assert "whole liquid market" in d["universe_measured_on"]


def test_a_run_of_one_session_is_refused():
    con = _db(names=4, sessions=120, drift=0.004)
    run = _run(con)
    run.equity = run.equity[:1]
    with pytest.raises(pnl.AttributionError, match="two sessions"):
        pnl.decompose(con, run, min_adv=0.0)


# --------------------------------------------------------------------------- by name
def test_per_name_pnl_sums_to_the_runs_own_result():
    """Cash flows plus the mark on what is still held is the whole P&L, so the names have to add up
    to the equity change."""
    con = _db(names=6, sessions=120, drift=0.004)
    run = _run(con)
    b = pnl.by_name(con, run)
    change = run.equity[-1]["equity"] - run.equity[0]["equity"]
    assert b["total_pnl"] == pytest.approx(change, rel=1e-6, abs=1.0)
    assert not b["unmarked"], f"unmarked positions: {b['unmarked']}"


def test_names_come_back_best_first_with_a_hit_rate():
    con = _db(names=6, sessions=120, drift=0.004)
    b = pnl.by_name(con, _run(con))
    pnls = [r["pnl"] for r in b["names"]]
    assert pnls == sorted(pnls, reverse=True)
    assert 0.0 <= b["hit_rate"] <= 1.0
    assert b["n_winners"] == sum(1 for r in b["names"] if r["pnl"] > 0)


def test_a_closed_position_carries_all_its_pnl_in_the_realised_column():
    con = _db(names=6, sessions=120, drift=0.004)
    b = pnl.by_name(con, _run(con))
    for r in b["names"]:
        if r["open_shares"] == 0:
            assert r["held_value"] == 0.0
            assert r["pnl"] == pytest.approx(r["realised_and_flows"])


# ------------------------------------------------------------------- concentration
def test_concentration_says_when_a_run_was_a_handful_of_bets():
    """The check nothing else in this project makes. A run whose gains came from one name is an
    anecdote with a Sharpe ratio."""
    one = [{"pnl": 100_000.0}, {"pnl": 500.0}, {"pnl": 200.0}, {"pnl": -900.0}]
    spread = [{"pnl": 25_000.0}, {"pnl": 25_000.0}, {"pnl": 25_000.0},
              {"pnl": 25_000.0}, {"pnl": -900.0}]

    c1, c2 = pnl.concentration(one), pnl.concentration(spread)
    assert c1["top1_share"] > 0.98
    assert c1["names_for_half_the_gains"] == 1
    assert c2["top1_share"] == pytest.approx(0.25)
    assert c2["names_for_half_the_gains"] == 2
    assert c1["top1_share"] > c2["top1_share"]


def test_concentration_is_measured_against_gross_gains_not_net_pnl():
    """A net total near zero makes every share explode, and the question - did the winning come from
    a few names - is not changed by the losers."""
    names = [{"pnl": 10_000.0}, {"pnl": 10_000.0}, {"pnl": -19_900.0}]
    c = pnl.concentration(names)
    assert c["gross_gain"] == pytest.approx(20_000.0)
    assert c["top1_share"] == pytest.approx(0.5)
    assert c["n_winners"] == 2


def test_concentration_of_a_run_that_never_gained_reports_none_rather_than_dividing():
    c = pnl.concentration([{"pnl": -5.0}, {"pnl": -3.0}])
    assert c["gross_gain"] == 0.0
    assert c["top1_share"] is None
    assert c["names_for_half_the_gains"] is None


# --------------------------------------------------------------------------- execution
def test_execution_separates_slippage_from_brokerage():
    con = _db(names=6, sessions=120, drift=0.004, minute_bars=True)
    run = _run(con, simulate_fills=True, restrict_to_minute_bars=True)
    e = pnl.execution(run)
    assert e["fills_measured"] > 0
    assert e["measured_share"] == pytest.approx(1.0)
    assert e["brokerage_inr"] > 0
    assert e["mean_shortfall_bps"] is not None


def test_a_close_filled_run_reports_no_measurement_rather_than_no_slippage():
    """Close fills are achieved at the close by construction, so a zero would be an absence of
    measurement dressed up as an absence of cost."""
    con = _db(names=6, sessions=120, drift=0.004)
    e = pnl.execution(_run(con, simulate_fills=False))
    assert e["fills_measured"] == 0
    assert e["mean_shortfall_bps"] is None
    assert "not because there was none" in e["why"]


def test_the_report_gives_all_three_together():
    con = _db(names=6, sessions=120, drift=0.004)
    r = pnl.report(con, _run(con), min_adv=0.0)
    assert set(r) == {"decomposition", "by_name", "execution"}
    assert r["decomposition"]["closes"] is True
