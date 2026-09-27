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


# --------------------------------------------------------- the universe is the experiment
def test_an_explicit_universe_restricts_the_ranking_not_just_the_result():
    """The distinction that cost this engine its first credible number. Ranking globally and
    *then* filtering leaves a book of whatever survives the filter - two to four names out of an
    intended twenty. A universe means rank within it."""
    con = _db(names=8, drift=0.004)
    keep = {f"INE{i:09d}" for i in range(4)}
    r = engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=119), rebalance=20, max_positions=3,
                   simulate_fills=False, eligible_isins=keep)
    assert r.trades, "the book should not be empty"
    assert {t.isin for t in r.trades} <= keep
    # Three of the four eligible names, not three of the global eight intersected down to one.
    assert len({t.isin for t in r.trades}) == 3


def test_an_explicit_universe_overrides_the_minute_bar_shortcut():
    """The shortcut picks names by turnover as measured when the ingest ran, which is after the
    window - so it is partly a list of what went up. An explicit universe has to win."""
    con = _db(names=8, drift=0.004, minute_bars=True)
    keep = {f"INE{i:09d}" for i in range(3)}
    r = engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=119), rebalance=20, max_positions=2,
                   simulate_fills=False, eligible_isins=keep,
                   restrict_to_minute_bars=True)
    assert {t.isin for t in r.trades} <= keep


def test_the_same_strategy_on_two_universes_is_two_experiments():
    """+9.00% excess on a universe selected with hindsight and +1.90% on one selected without it
    are different results, and an id that collapsed them would let the first be reported as the
    second."""
    con = _db(names=8, drift=0.004)
    kw = dict(feature="dist_52w_high", start=START, end=START + timedelta(days=119),
              rebalance=20, max_positions=3, simulate_fills=False)
    a = engine.run(con, **kw, eligible_isins={f"INE{i:09d}" for i in range(4)})
    b = engine.run(con, **kw, eligible_isins={f"INE{i:09d}" for i in range(4, 8)})
    c = engine.run(con, **kw, eligible_isins={f"INE{i:09d}" for i in range(4)})
    assert a.experiment_id != b.experiment_id
    assert a.experiment_id == c.experiment_id, "the same universe must reproduce"


def test_the_control_is_computed_on_the_universe_that_was_traded():
    """An excess is only readable against the universe the strategy actually chose from. Measuring
    a restricted book against the whole market is how universe selection becomes alpha."""
    con = _db(names=8, drift=0.004)
    keep = {f"INE{i:09d}" for i in range(4)}
    r = engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=119), rebalance=20, max_positions=3,
                   simulate_fills=False, eligible_isins=keep)
    assert r.control["universe_names"] <= len(keep)
    s = r.summary()
    assert s["excess_over_universe"] == pytest.approx(
        s["total_return"] - s["universe_return"])


def test_a_name_that_delists_does_not_flatter_the_control():
    """The bug that made the eleven-year run unreadable.

    Pricing the universe at both ends of the window keeps only the names that still had a price at
    the end, so every delisting is silently removed from the control - and the control becomes an
    index of survivors, which no strategy can beat. Here one of four names stops trading halfway
    after falling; a point-to-point control would drop it and report the remaining three, while a
    chained daily mean has to carry its decline.
    """
    con = _db(names=4, sessions=120, drift=0.004)
    dead = "INE000000000"
    days = [r[0] for r in con.execute(
        "SELECT DISTINCT business_date FROM adjusted_prices ORDER BY 1").fetchall()]
    con.execute("UPDATE adjusted_prices SET close_adj = close_adj * 0.4 "
                "WHERE isin = ? AND business_date >= ?", [dead, days[59]])
    con.execute("DELETE FROM adjusted_prices WHERE isin = ? AND business_date > ?",
                [dead, days[60]])

    r = engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=119), rebalance=20, max_positions=2,
                   simulate_fills=False)
    with_dead = r.control["universe_return"]

    con.execute("DELETE FROM adjusted_prices WHERE isin = ?", [dead])
    clean = engine.run(con, feature="dist_52w_high", start=START,
                       end=START + timedelta(days=119), rebalance=20, max_positions=2,
                       simulate_fills=False).control["universe_return"]

    assert with_dead < clean, (
        f"the universe containing a name that collapsed and delisted returned {with_dead:.4f}, "
        f"which must be worse than the same universe without it ({clean:.4f})")


def test_the_control_counts_every_name_that_was_eligible_not_only_the_ones_that_lasted():
    con = _db(names=5, sessions=120, drift=0.004)
    days = [r[0] for r in con.execute(
        "SELECT DISTINCT business_date FROM adjusted_prices ORDER BY 1").fetchall()]
    con.execute("DELETE FROM adjusted_prices WHERE isin = ? AND business_date > ?",
                ["INE000000000", days[60]])
    r = engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=119), rebalance=20, max_positions=2,
                   simulate_fills=False)
    assert r.control["universe_names"] == 5


# ----------------------------------------------------- the engine must not have its own edge
def test_a_book_holding_its_whole_universe_reproduces_the_control():
    """The test that found the missing trim leg, and the strongest guard in this file.

    If the book holds every eligible name at equal weight, it *is* the control by construction, so
    its excess must be approximately zero. Any gap is the engine's own drift, and it biases every
    excess the engine reports. Before the trim leg existed this came back at -140.27% over eleven
    years of real data, because a book that can buy up to its target weight but never sell down to
    it is not equal-weight - it is buy-and-hold with additions.
    """
    con = _db(names=10, sessions=200, drift=0.01)
    r = engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=199), rebalance=20, max_positions=10,
                   simulate_fills=False, min_adv=0.0)
    s = r.summary()
    assert s["universe_return"] is not None
    # Costs and the cash buffer are real and are allowed to cost the book something; drift is not.
    assert s["excess_over_universe"] == pytest.approx(0.0, abs=0.05), (
        f"a book holding its whole universe returned {s['total_return']:+.2%} against a control "
        f"of {s['universe_return']:+.2%}; the {s['excess_over_universe']:+.2%} gap is engine drift")


def test_a_winner_is_trimmed_back_toward_its_target_weight():
    """One name runs away from the rest. An equal-weight book sells some of it at the rebalance;
    a book that only ever buys lets it become the portfolio."""
    con = _db(names=4, sessions=140, drift=0.0)
    days = [r[0] for r in con.execute(
        "SELECT DISTINCT business_date FROM adjusted_prices ORDER BY 1").fetchall()]
    con.execute("UPDATE adjusted_prices SET close_adj = close_adj * 4 "
                "WHERE isin = ? AND business_date >= ?", ["INE000000000", days[40]])

    r = engine.run(con, feature="dist_52w_high", start=START,
                   end=START + timedelta(days=139), rebalance=20, max_positions=4,
                   simulate_fills=False, min_adv=0.0)
    sells = [t for t in r.trades if t.side == "SELL" and t.isin == "INE000000000"]
    assert sells, "the name that quadrupled was never trimmed"

    # And the book it ends with is not dominated by that one name.
    final = r.equity[-1]
    assert final["positions"] == 4


def test_a_drift_inside_the_band_is_left_alone():
    """The band has to be wide enough to ignore the cost drag and narrow enough to catch a real
    divergence. A name up 10% against a 20% band is not traded; the same name up 60% is."""
    con = _db(names=4, sessions=140, drift=0.0)
    days = [r[0] for r in con.execute(
        "SELECT DISTINCT business_date FROM adjusted_prices ORDER BY 1").fetchall()]

    def trims(multiple):
        c = _db(names=4, sessions=140, drift=0.0)
        c.execute("UPDATE adjusted_prices SET close_adj = close_adj * ? "
                  "WHERE isin = ? AND business_date >= ?",
                  [multiple, "INE000000000", days[40]])
        r = engine.run(c, feature="dist_52w_high", start=START,
                       end=START + timedelta(days=139), rebalance=20, max_positions=4,
                       simulate_fills=False, min_adv=0.0)
        return [t for t in r.trades
                if t.side == "SELL" and t.isin == "INE000000000"]

    assert engine.REBALANCE_BAND == 0.20
    assert not trims(1.10), "a 10% drift is inside the band and must not be traded"
    assert trims(1.60), "a 60% drift is outside the band and must be trimmed"


def test_the_calibration_reports_a_floor_and_the_cash_it_leaves_behind():
    """The floor is not a pass/fail - it is the resolution of the instrument, and it has to be
    reported rather than assumed small. On eleven years of real data it is -52.52% gross, which is
    larger than every excess this engine has reported."""
    con = _db(names=8, sessions=200, drift=0.01)
    c = engine.calibrate(con, start=START, end=START + timedelta(days=199),
                         min_adv=0.0, capital_inr=1e8)
    assert c["floor"] is not None
    assert 0.0 <= c["uninvested"] < 0.5
    assert c["periods"] and c["names"] == 8
    # Gross, so the floor cannot be explained away as brokerage.
    assert abs(c["floor"]) == pytest.approx(
        abs(c["book_return"] - c["universe_return"]), abs=1e-9)


def test_costs_can_be_switched_off_only_explicitly_and_are_recorded_in_the_run():
    """A gross run must be impossible to mistake for a result, so the flag is part of the config
    the experiment id hashes and appears in the summary."""
    con = _db(names=6, drift=0.004)
    kw = dict(feature="dist_52w_high", start=START, end=START + timedelta(days=119),
              rebalance=20, max_positions=4, simulate_fills=False)
    net = engine.run(con, **kw)
    gross = engine.run(con, **kw, charge_costs=False)
    assert net.summary()["charge_costs"] is True
    assert gross.summary()["charge_costs"] is False
    assert gross.summary()["costs_inr"] == 0.0
    assert net.summary()["costs_inr"] > 0.0
    assert net.experiment_id != gross.experiment_id
    assert gross.summary()["total_return"] > net.summary()["total_return"]
