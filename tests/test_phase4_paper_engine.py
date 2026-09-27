"""The paper engine, and the four separations that make it a trading system rather than a backtest.

A paper engine is the one component where an accounting error is indistinguishable from alpha. If
cash can go negative the book is levered; if a sell credits more than it should the equity curve
rises; if an order silently completes the turnover is understated. So most of these tests are
invariants rather than behaviours, checked on a constructed market where the right answer is
arithmetic.
"""
from __future__ import annotations

import inspect
from datetime import date, timedelta

import duckdb
import pytest

from financial_brain.paper import engine

START = date(2024, 1, 1)


def _db(*, names=6, sessions=120, drift=0.0, minute_bars=True, price0=100.0):
    """A market with a known price path and, optionally, minute bars for every name.

    Name i gets drift proportional to i, so the cross-sectional ranking is deterministic and the
    engine's target book is predictable.
    """
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE adjusted_prices (business_date DATE, isin VARCHAR,
                   lineage VARCHAR, close_adj DOUBLE, turnover DOUBLE,
                   traded_volume BIGINT)""")
    con.execute("""CREATE TABLE features (business_date DATE, lineage VARCHAR,
                   adv20 DOUBLE, dist_52w_high DOUBLE, mom_12_1 DOUBLE)""")
    con.execute("""CREATE TABLE security_lineage (isin VARCHAR, lineage VARCHAR)""")
    con.execute("""CREATE TABLE universe_snapshots (business_date DATE, isin VARCHAR,
                   exchange VARCHAR, instrument_type VARCHAR, turnover DOUBLE)""")
    con.execute("""CREATE TABLE promoter_groups (group_id VARCHAR, anchor VARCHAR,
                   member VARCHAR)""")
    con.execute("""CREATE TABLE minute_bars (instrument_token BIGINT,
                   tradingsymbol VARCHAR, isin VARCHAR, ts TIMESTAMP, open DOUBLE,
                   high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT,
                   source VARCHAR)""")
    con.execute("""CREATE TABLE index_levels (business_date DATE, index_name VARCHAR,
                   close_level DOUBLE)""")

    # Rows are generated **in SQL**, not in Python. This project already measured DuckDB's
    # `executemany` at roughly 800 rows/sec against 153,000/s for a staged bulk load, and a
    # fixture with 21,600 minute bars therefore took about half a minute each - sixteen tests of
    # that is a file nobody waits for, which is the same as not having the tests.
    #
    # `generate_series` builds the same rows inside the engine in milliseconds. Name i is given
    # drift proportional to i so the cross-sectional ranking is deterministic.
    con.execute(f"""
        INSERT INTO security_lineage
        SELECT 'INE' || LPAD(i::VARCHAR, 9, '0'), 'L' || i
        FROM generate_series(0, {names - 1}) t(i)
    """)
    con.execute(f"""
        INSERT INTO adjusted_prices
        SELECT (DATE '{START}' + INTERVAL (k) DAY)::DATE, 'INE' || LPAD(i::VARCHAR, 9, '0'), 'L' || i,
               {price0} * (1 + {drift} * i * k / {sessions}), 1e9, 1000000
        FROM generate_series(0, {names - 1}) a(i), generate_series(0, {sessions - 1}) b(k)
    """)
    con.execute(f"""
        INSERT INTO features
        SELECT (DATE '{START}' + INTERVAL (k) DAY)::DATE, 'L' || i, 1e9, i::DOUBLE, i::DOUBLE
        FROM generate_series(0, {names - 1}) a(i), generate_series(0, {sessions - 1}) b(k)
    """)
    con.execute(f"""
        INSERT INTO universe_snapshots
        SELECT (DATE '{START}' + INTERVAL (k) DAY)::DATE, 'INE' || LPAD(i::VARCHAR, 9, '0'), 'NSE', 'STK', 1e9
        FROM generate_series(0, {names - 1}) a(i), generate_series(0, {sessions - 1}) b(k)
    """)
    # An index series, so the engine's benchmark control has something to read. Without it the
    # control must still return the universe figure, which is the one that matters.
    con.execute(f"""
        INSERT INTO index_levels
        SELECT (DATE '{START}' + INTERVAL (k) DAY)::DATE, 'Nifty 500', 1000.0 * (1 + 0.0001 * k)
        FROM generate_series(0, {sessions - 1}) b(k)
    """)
    if minute_bars:
        con.execute(f"""
            INSERT INTO minute_bars
            WITH px AS (
                SELECT i, k, {price0} * (1 + {drift} * i * k / {sessions}) AS p
                FROM generate_series(0, {names - 1}) a(i),
                     generate_series(0, {sessions - 1}) b(k)
            )
            SELECT px.i, 'SYM' || px.i, 'INE' || LPAD(px.i::VARCHAR, 9, '0'),
                   (DATE '{START}' + INTERVAL (px.k) DAY)::TIMESTAMP
                       + INTERVAL (9) HOUR + INTERVAL (15) MINUTE + INTERVAL (m) MINUTE,
                   px.p, px.p, px.p, px.p, 50000, 'test'
            FROM px, generate_series(0, 29) c(m)
        """)
    return con


# ------------------------------------------------------------- the four separations
def test_the_signal_is_read_before_the_session_the_order_fills_in():
    """A backtest that fills at the close that generated the signal has bought a day of hindsight
    per trade."""
    con = _db(drift=0.01)
    r = engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=119), rebalance=20, max_positions=3)
    assert r.rebalances
    for rb in r.rebalances:
        assert rb["signal_from"] < rb["session"], "signal must predate the fill session"


def test_a_name_already_held_is_not_re_bought():
    """Turnover has to come from the diff against holdings, not from an assumption."""
    con = _db(names=4, drift=0.0)
    r = engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=119), rebalance=20, max_positions=4)
    # With a constant ranking and constant prices, only the first rebalance should trade.
    later = [rb for rb in r.rebalances[1:]]
    assert all(rb["trades"] == 0 for rb in later), \
        f"a static book re-traded: {[rb['trades'] for rb in later]}"


def test_an_order_that_cannot_fill_leaves_a_smaller_position_not_the_intended_one():
    con = _db(names=3, minute_bars=True)
    # Tiny minute volume: 10% participation over 80 minutes cannot fill a large order.
    con.execute("UPDATE minute_bars SET volume = 20")
    r = engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=119), rebalance=20, max_positions=3,
                   capital_inr=50_000_000.0)
    partial = [t for t in r.trades if t.unfilled > 0]
    assert partial, "a constrained market should leave unfilled orders"
    assert all(t.shares < t.intended_shares for t in partial)


def test_equity_is_cash_plus_holdings_every_session():
    con = _db(drift=0.005)
    r = engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=119), rebalance=20, max_positions=3)
    for e in r.equity:
        assert e["equity"] == pytest.approx(e["cash"] + e["holdings_value"], rel=1e-9)


# ------------------------------------------------------------------ cash discipline
def test_cash_never_goes_negative():
    """A paper engine that lets cash go negative is quietly levered, and the leverage shows up as
    return."""
    con = _db(names=8, drift=0.01)
    r = engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=119), rebalance=20, max_positions=8)
    assert all(e["cash"] >= -1e-6 for e in r.equity), \
        f"min cash {min(e['cash'] for e in r.equity):,.2f}"


def test_the_book_never_exceeds_one_hundred_percent_invested():
    con = _db(names=8, drift=0.01)
    r = engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=119), rebalance=20, max_positions=8)
    assert max(e["invested"] for e in r.equity) <= 1.0 + 1e-9


def test_sells_settle_before_buys_are_sized():
    """Buying before the sells have settled spends cash the book does not have - leverage
    introduced by ordering rather than by intent."""
    src = inspect.getsource(engine._rebalance)
    sell_at = src.index("side=sim.SELL")
    buy_at = src.index("side=sim.BUY")
    assert sell_at < buy_at


def test_a_flat_market_with_no_costs_preserves_capital():
    con = _db(names=4, drift=0.0)
    r = engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=119), rebalance=20, max_positions=4)
    opening, closing = r.equity[0]["equity"], r.equity[-1]["equity"]
    # Only costs should be lost in a flat market; they are real, so equity falls a little.
    assert closing < opening
    assert closing > opening * 0.97, f"{opening:,.0f} -> {closing:,.0f} lost too much"
    assert r.summary()["costs_inr"] > 0


# ---------------------------------------------------------------------------- fills
def test_fills_are_simulated_when_minute_bars_exist_and_labelled_when_not():
    with_bars = _db(names=4, minute_bars=True)
    r1 = engine.run(with_bars, feature="dist_52w_high", start=START,
                    end=START + timedelta(days=119), rebalance=20, max_positions=4)
    assert r1.summary()["simulated_fill_share"] == pytest.approx(1.0)
    assert all(t.fill_kind == engine.SIMULATED_FILL for t in r1.trades)

    without = _db(names=4, minute_bars=False)
    r2 = engine.run(without, feature="dist_52w_high", start=START,
                    end=START + timedelta(days=119), rebalance=20, max_positions=4)
    assert r2.summary()["simulated_fill_share"] == pytest.approx(0.0)
    assert all(t.fill_kind == engine.CLOSE_FILL for t in r2.trades)


def test_the_coverage_number_is_reported_rather_than_assumed():
    """A run whose fills were 80% close-priced is not a run that measured execution, and the
    number has to say so rather than the caveat living in someone's memory."""
    con = _db(names=4, minute_bars=True)
    con.execute("DELETE FROM minute_bars WHERE isin IN ('INE000000000','INE000000001')")
    r = engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=119), rebalance=20, max_positions=4)
    share = r.summary()["simulated_fill_share"]
    assert 0.0 < share < 1.0, share
    kinds = {t.fill_kind for t in r.trades}
    assert kinds == {engine.SIMULATED_FILL, engine.CLOSE_FILL}


def test_restricting_to_minute_bars_gives_complete_coverage():
    con = _db(names=6, minute_bars=True)
    con.execute("DELETE FROM minute_bars WHERE isin = 'INE000000005'")
    r = engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=119), rebalance=20, max_positions=6,
                   restrict_to_minute_bars=True)
    assert r.summary()["simulated_fill_share"] == pytest.approx(1.0)
    assert all(t.isin != "INE000000005" for t in r.trades)


# ------------------------------------------------------------------ reproducibility
def test_the_experiment_id_is_a_hash_of_the_configuration():
    """Two runs with the same id must be the same experiment - the precondition for
    reconstructing a result rather than recomputing something similar."""
    a = {"feature": "x", "start": START, "rebalance": 20}
    b = {"rebalance": 20, "start": START, "feature": "x"}
    assert engine.experiment_id(a) == engine.experiment_id(b)
    assert engine.experiment_id({**a, "rebalance": 21}) != engine.experiment_id(a)
    assert engine.experiment_id(a).startswith("EXP-")


def test_the_same_configuration_reproduces_the_same_run():
    con = _db(names=4, drift=0.004)
    kw = dict(feature="dist_52w_high", start=START, end=START + timedelta(days=119),
              rebalance=20, max_positions=3)
    a = engine.run(con, **kw)
    b = engine.run(con, **kw)
    assert a.experiment_id == b.experiment_id
    assert [e["equity"] for e in a.equity] == [e["equity"] for e in b.equity]
    assert len(a.trades) == len(b.trades)


# ------------------------------------------------------------------------- refusals
def test_too_short_a_window_is_refused():
    con = _db(sessions=20)
    with pytest.raises(engine.PaperError):
        engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=19), rebalance=20)


def test_a_suspended_holding_is_marked_at_its_last_value_not_dropped():
    """A holding that stops trading is not free, and dropping it from the mark would make the
    equity curve rise when a position went dark."""
    con = _db(names=3, sessions=120)
    cutoff = START + timedelta(days=80)
    con.execute("DELETE FROM adjusted_prices WHERE isin = 'INE000000002' "
                "AND business_date > ?", [cutoff])
    r = engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=119), rebalance=20, max_positions=3)
    assert sum(e["stale_marks"] for e in r.equity) > 0
    for e in r.equity:
        assert e["equity"] == pytest.approx(e["cash"] + e["holdings_value"], rel=1e-9)


def test_max_drawdown_is_never_positive():
    con = _db(names=4, drift=0.01)
    r = engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=119), rebalance=20, max_positions=4)
    assert r.summary()["max_drawdown"] <= 0.0
