"""The execution simulator and the money gate, pinned against constructed sessions.

A fill simulator is easy to get subtly wrong in the flattering direction: start in the minute that
produced the signal, fill more than traded, sell more than was bought, price at the close of a
minute that happened to end well. Each of those is a test below, with a session built so the right
answer is arithmetic rather than judgement.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import duckdb
import pytest

from financial_brain.evaluation import money_gate
from financial_brain.execution import simulator as S

SESSION = date(2026, 6, 15)


def _db(bars):
    """bars: list of (open, high, low, close, volume), one per minute from 09:15."""
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE minute_bars (instrument_token BIGINT,
                   tradingsymbol VARCHAR, isin VARCHAR, ts TIMESTAMP, open DOUBLE,
                   high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT,
                   source VARCHAR)""")
    t0 = datetime(SESSION.year, SESSION.month, SESSION.day, 9, 15)
    for i, (o, h, lo, c, v) in enumerate(bars):
        con.execute("INSERT INTO minute_bars VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [1, "TEST", "INE000TEST01", t0 + timedelta(minutes=i),
                     o, h, lo, c, v, "test"])
    con.execute("""CREATE TABLE evaluation_runs (run_at TIMESTAMP, feature VARCHAR,
                   horizon INTEGER, dates INTEGER, mean_ic DOUBLE, ic_t DOUBLE,
                   sharpe DOUBLE, deflated_sharpe DOUBLE, verdict VARCHAR,
                   params VARCHAR)""")
    return con


def _flat(n=100, price=100.0, volume=10_000):
    return [(price, price, price, price, volume)] * n


# --------------------------------------------------------------------- the simulator
def test_a_fill_may_not_begin_in_the_minute_that_produced_the_signal():
    """The cheapest look-ahead available in an execution study."""
    bars = _flat(10)
    con = _db(bars)
    f = S.execute(con, symbol="TEST", session=SESSION, side=S.BUY,
                  target_shares=500, decision_price=100.0, start_minute=3)
    assert f.first_minute == 3
    assert all(sl["minute"] >= 3 for sl in f.slices if "minute" in sl)


def test_an_order_cannot_take_more_than_the_volume_that_traded():
    con = _db(_flat(5, volume=1_000))
    # 10% of 1,000 is 100 a minute over five minutes: 500 is the most obtainable.
    f = S.execute(con, symbol="TEST", session=SESSION, side=S.BUY,
                  target_shares=5_000, decision_price=100.0, participation=0.10)
    assert f.filled_shares == 500
    assert f.unfilled == 4_500
    assert f.fill_rate == pytest.approx(0.1)


def test_participation_controls_how_long_a_fill_takes():
    con = _db(_flat(60, volume=10_000))
    fast = S.execute(con, symbol="TEST", session=SESSION, side=S.BUY,
                     target_shares=3_000, decision_price=100.0, participation=0.30)
    slow = S.execute(con, symbol="TEST", session=SESSION, side=S.BUY,
                     target_shares=3_000, decision_price=100.0, participation=0.05)
    assert fast.filled_shares == slow.filled_shares == 3_000
    assert fast.minutes < slow.minutes


def test_the_price_is_the_typical_price_not_the_close():
    """A minute's close is one print; an order working through the minute gets nearer the
    average. Using the close would make a fill look better or worse depending on which way the
    minute happened to end, which is noise the order did not experience."""
    # A minute whose close is far from its middle: (H+L+C)/3 = (110+90+110)/3 = 103.33
    con = _db([(100.0, 110.0, 90.0, 110.0, 10_000)])
    f = S.execute(con, symbol="TEST", session=SESSION, side=S.BUY,
                  target_shares=100, decision_price=100.0)
    assert f.achieved == pytest.approx((110 + 90 + 110) / 3)
    assert f.achieved != 110.0


def test_shortfall_is_signed_so_positive_always_hurts():
    con = _db([(100.0, 102.0, 102.0, 102.0, 10_000)])
    buy = S.execute(con, symbol="TEST", session=SESSION, side=S.BUY,
                    target_shares=100, decision_price=100.0)
    sell = S.execute(con, symbol="TEST", session=SESSION, side=S.SELL,
                     target_shares=100, decision_price=100.0)
    # Filling at 102 costs a buyer and benefits a seller.
    assert buy.shortfall_bps > 0
    assert sell.shortfall_bps < 0
    assert buy.shortfall_bps == pytest.approx(-sell.shortfall_bps)


def test_an_order_too_late_to_finish_is_refused_not_filled():
    """An order that cannot complete leaves a position the signal did not ask for."""
    con = _db(_flat(380))
    f = S.execute(con, symbol="TEST", session=SESSION, side=S.BUY,
                  target_shares=100, decision_price=100.0, start_minute=370)
    assert f.filled_shares == 0
    assert any("refused" in sl for sl in f.slices)


def test_the_exit_sells_what_was_bought_not_what_was_wanted():
    """An exit sized to the intended quantity sells stock the strategy never owned, and it
    flatters the result by exactly the amount the entry fell short."""
    con = _db(_flat(80, volume=1_000))
    r = S.round_trip(con, symbol="TEST", session=SESSION, entry_minute=1,
                     decision_price=100.0, target_shares=100_000)
    assert r["entry"]["filled_shares"] < 100_000
    assert r["exit"]["target_shares"] == r["entry"]["filled_shares"]
    assert r["held_shares"] == r["entry"]["filled_shares"]


def test_a_round_trip_with_nothing_filled_has_no_return():
    con = _db(_flat(380))
    r = S.round_trip(con, symbol="TEST", session=SESSION, entry_minute=370,
                     decision_price=100.0, target_shares=100)
    assert r["gross_return"] is None
    assert r["exit"] is None


def test_a_flat_session_produces_no_gross_return():
    con = _db(_flat(120))
    r = S.round_trip(con, symbol="TEST", session=SESSION, entry_minute=1,
                     decision_price=100.0, target_shares=1_000)
    assert r["gross_return"] == pytest.approx(0.0, abs=1e-12)


def test_capacity_is_the_sum_of_what_participation_allows():
    con = _db(_flat(10, price=50.0, volume=2_000))
    cap = S.capacity(con, symbol="TEST", session=SESSION, participation=0.10)
    assert cap["shares"] == 10 * 200
    assert cap["notional"] == pytest.approx(10 * 200 * 50.0)
    assert cap["session_volume"] == 20_000


def test_bad_arguments_are_refused():
    con = _db(_flat(10))
    for kw in ({"side": "HOLD"}, {"target_shares": 0}, {"participation": 0.0},
               {"participation": 1.5}):
        with pytest.raises(S.ExecutionError):
            S.execute(con, symbol="TEST", session=SESSION,
                      **{"side": S.BUY, "target_shares": 100,
                         "decision_price": 100.0, **kw})


def test_no_bars_yields_an_empty_fill_rather_than_an_error():
    con = _db([])
    f = S.execute(con, symbol="TEST", session=SESSION, side=S.BUY,
                  target_shares=100, decision_price=100.0)
    assert f.filled_shares == 0 and f.achieved is None


# --------------------------------------------------------------------- the money gate
def _fw(**over):
    base = {"gates": {"significance": True, "deflated_sharpe": True, "walk_forward": True,
                      "regime": True, "costs": True, "capacity": True},
            "ic_t": 4.2, "deflated_sharpe": 0.97, "turnover": 0.3,
            "round_trip": 0.008, "net_per_period": 0.004, "dates": 100,
            "avg_names": 200, "ic_by_year": {2024: 0.01}, "ic_by_regime": {"RISK_ON": 0.01},
            "filled_no_outcome": 0}
    base.update(over)
    return base


def test_nothing_reaches_capital_with_an_unavailable_check():
    """A check nobody can run is not a check that passed."""
    con = _db(_flat(2))
    v = money_gate.evaluate(con, strategy="s", firewall_result=_fw(),
                            null_separation=2.0, net_t=5.0, oos_rebalances=50,
                            paper_sessions=500, execution=None,
                            portfolio_verdict="ALLOW")
    assert v.verdict == "NO_CAPITAL"
    assert {c.name for c in v.unavailable} >= {"spread", "sector_exposure",
                                               "execution_simulation"}


def test_ic_significance_and_net_significance_are_asked_separately():
    """vol_60 has the largest IC on record at +12.30 with negative alpha. A money gate that
    asked only about the ordering would approve on an edge nobody can harvest."""
    con = _db(_flat(2))
    v = money_gate.evaluate(con, strategy="s", firewall_result=_fw(ic_t=12.3),
                            null_separation=2.0, net_t=0.4, oos_rebalances=50)
    by = {c.name: c for c in v.checks}
    assert by["ic_significance"].status == "PASS"
    assert by["net_significance"].status == "FAIL"
    assert v.verdict == "NO_CAPITAL"


def test_a_missing_net_t_is_unavailable_not_passed():
    con = _db(_flat(2))
    v = money_gate.evaluate(con, strategy="s", firewall_result=_fw(), net_t=None)
    assert {c.name for c in v.unavailable} >= {"net_significance"}


def test_an_unrun_check_is_unavailable_and_a_failed_one_is_fail():
    """The two call for entirely different work and must not read the same."""
    con = _db(_flat(2))
    absent = money_gate.evaluate(con, strategy="s", firewall_result=_fw(),
                                 implementability=None)
    failed = money_gate.evaluate(con, strategy="s", firewall_result=_fw(),
                                 implementability={"verdict": "FAIL",
                                                   "summary": "too thin"})
    assert {c.name for c in absent.unavailable} >= {"liquidity"}
    assert {c.name for c in failed.failed} >= {"liquidity"}


def test_the_gate_has_no_warning_state():
    """A reservation is how a failing strategy reaches capital: someone reads it, decides it is
    acceptable, and the gate has become advice."""
    con = _db(_flat(2))
    v = money_gate.evaluate(con, strategy="s", firewall_result=_fw())
    assert {c.status for c in v.checks} <= {"PASS", "FAIL", "UNAVAILABLE"}
    assert v.verdict in ("CAPITAL", "NO_CAPITAL")


def test_the_bar_is_read_from_the_ledger_not_stored():
    con = _db(_flat(2))
    first = money_gate._bar(con)
    con.executemany("INSERT INTO evaluation_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [(None, "f", 20, 10, 0.0, 2.0, 0.1, 0.5, "REJECT", "{}")
                     for _ in range(60)])
    assert money_gate._bar(con) > first
