"""Portfolio-level cost and the cost band: the two bugs that made them necessary.

One bucket charged to a whole book is wrong in both directions at once, and which
direction depends on where the signal's holdings sit in the liquidity ranking:

* the intraday systems square off in the session (MIS, STT sell-leg only) and were charged
  delivery STT on both legs, at the ``mid`` impact bucket, to a name set that is entirely
  mega and large - over-stated;
* a daily signal's top quintile is half small and micro, and ``mid`` under-states it.

These tests pin the direction of each, so a future refactor that quietly reintroduces a
flat charge fails rather than producing a plausible number.
"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from financial_brain.costs import book
from financial_brain.costs.india import CostModel, Segment
from financial_brain.evaluation import band


# --------------------------------------------------------------- the segment fact
def test_intraday_segment_is_far_cheaper_than_delivery():
    """STT is 10 bps on both delivery legs and 2.5 bps on the intraday sell leg only, so
    charging delivery to a position squared off in the session over-states it."""
    m = CostModel()
    d = m.round_trip(turnover=1e6, segment=Segment.DELIVERY, bucket="mid")["bps"]
    i = m.round_trip(turnover=1e6, segment=Segment.INTRADAY, bucket="mid")["bps"]
    assert d - i == pytest.approx(17.1, abs=0.2)     # 20 bps STT less 2.5, plus stamp


def test_bucket_dominates_the_intraday_round_trip():
    """Impact, not tax, is the whole spread between a mega name and a mid one - which is
    why charging one bucket to a book of megas over-states it fourfold."""
    m = CostModel()
    rt = {b: m.round_trip(turnover=1e6, segment=Segment.INTRADAY, bucket=b)["bps"]
          for b in ("mega", "large", "mid")}
    assert rt["mid"] / rt["mega"] > 3.5
    # And against what the first run actually charged:
    delivery_mid = m.round_trip(turnover=1e6, segment=Segment.DELIVERY, bucket="mid")["bps"]
    assert delivery_mid / rt["mega"] > 5


# ------------------------------------------------------------------ per-name buckets
def _db(names_by_adv, dates):
    """names_by_adv: lineages ordered most liquid first; every date shares the order.

    The ranking population is ``universe_snapshots`` - the exchange's own list - because the
    impact tiers are absolute national ranks. Ranking inside a filtered set was the bug this
    module replaced, so the fixture has to carry a real universe for the test to mean
    anything.
    """
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE universe_snapshots (business_date DATE, isin VARCHAR,
                   exchange VARCHAR, instrument_type VARCHAR, turnover DOUBLE)""")
    con.execute("""CREATE TABLE security_lineage (isin VARCHAR, lineage VARCHAR)""")
    for i, lin in enumerate(names_by_adv):
        con.execute("INSERT INTO security_lineage VALUES (?,?)", [f"I{lin}", lin])
        con.executemany("INSERT INTO universe_snapshots VALUES (?,?,?,?,?)",
                        [(d, f"I{lin}", "NSE", "STK", 1e12 - i * 1e6) for d in dates])
    return con


def test_buckets_follow_that_date_s_own_ranking():
    dates = [date(2026, 1, 5), date(2026, 2, 2)]
    names = [f"L{i:04d}" for i in range(1600)]
    con = _db(names, dates)
    got = book.buckets(con, dates, ["L0000", "L0150", "L0500", "L1000", "L1550"])
    assert got[(dates[0], "L0000")] == "mega"
    assert got[(dates[0], "L0150")] == "large"
    assert got[(dates[0], "L0500")] == "mid"
    assert got[(dates[0], "L1000")] == "small"
    assert got[(dates[1], "L1550")] == "micro"


def test_a_small_cap_book_costs_more_than_the_flat_mid_charge():
    """The direction that matters: the flat charge ran in the strategies' favour."""
    dates = [date(2026, 1, 5)]
    names = [f"L{i:04d}" for i in range(1600)]
    con = _db(names, dates)
    series = [{"date": dates[0], "top": ["L0800", "L0900", "L1200", "L1550"]}]
    held = book.per_rebalance(con, series)[0]
    flat = CostModel().round_trip(turnover=1e6, bucket="mid")["bps"] / 10_000
    assert held > flat
    mix = book.mix(con, series)
    assert mix["small"] == pytest.approx(0.75)
    assert mix["micro"] == pytest.approx(0.25)


def test_a_mega_book_costs_less_than_the_flat_mid_charge():
    dates = [date(2026, 1, 5)]
    names = [f"L{i:04d}" for i in range(1600)]
    con = _db(names, dates)
    series = [{"date": dates[0], "top": ["L0000", "L0010", "L0050"]}]
    held = book.per_rebalance(con, series)[0]
    flat = CostModel().round_trip(turnover=1e6, bucket="mid")["bps"] / 10_000
    assert held < flat


def test_an_unranked_name_is_dropped_not_defaulted():
    """A default would be a number nobody measured, which is how the flat charge got in."""
    dates = [date(2026, 1, 5)]
    con = _db([f"L{i:04d}" for i in range(400)], dates)
    series = [{"date": dates[0], "top": ["L0000", "ZZZZ"]},
              {"date": dates[0], "top": ["QQQQ"]}]
    got = book.per_rebalance(con, series)
    mega = CostModel().round_trip(turnover=1e6, bucket="mega")["bps"] / 10_000
    assert got[0] == pytest.approx(mega)      # ZZZZ ignored, not charged 'mid'
    assert got[1] is None                     # nothing bucketed at all


# ------------------------------------------------------------------------- the band
def _panel_db(n_names=60, n_dates=30, horizon=5):
    """A panel where the signal is genuinely informative and the ranking churns, so the
    band has something to suppress."""
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE adjusted_prices (business_date DATE, lineage VARCHAR,
                   close_adj DOUBLE)""")
    con.execute("""CREATE TABLE features (business_date DATE, lineage VARCHAR,
                   adv20 DOUBLE, mom_12_1 DOUBLE)""")
    con.execute("""CREATE TABLE universe_snapshots (business_date DATE, isin VARCHAR,
                   exchange VARCHAR, instrument_type VARCHAR, turnover DOUBLE)""")
    con.execute("""CREATE TABLE security_lineage (isin VARCHAR, lineage VARCHAR)""")
    d0 = date(2026, 1, 1)
    days = [d0 + timedelta(days=i) for i in range(n_dates * horizon + horizon + 2)]
    rng = 12345
    price = {f"L{i:03d}": 100.0 for i in range(n_names)}
    for k, d in enumerate(days):
        for i in range(n_names):
            lin = f"L{i:03d}"
            rng = (1103515245 * rng + 12345) % (2 ** 31)
            u = rng / 2 ** 31 - 0.5
            # A signal that predicts: names with a high index drift up.
            price[lin] *= 1 + 0.02 * u + 0.0004 * (i - n_names / 2) / n_names
            con.execute("INSERT INTO adjusted_prices VALUES (?,?,?)", [d, lin, price[lin]])
            con.execute("INSERT INTO features VALUES (?,?,?,?)",
                        [d, lin, 1e12 - i * 1e7, (i - n_names / 2) / n_names + 0.3 * u])
            con.execute("INSERT INTO universe_snapshots VALUES (?,?,?,?,?)",
                        [d, f"I{lin}", "NSE", "STK", 1e12 - i * 1e7])
        del k
    for i in range(n_names):
        con.execute("INSERT INTO security_lineage VALUES (?,?)",
                    [f"IL{i:03d}", f"L{i:03d}"])
    return con


def test_the_band_cuts_turnover_and_never_raises_it():
    con = _panel_db()
    plain = band.run(con, "mom_12_1", 5, positions=10, banded=False, min_adv=0)
    fenced = band.run(con, "mom_12_1", 5, positions=10, banded=True, min_adv=0)
    assert plain["dates"] > 5 and fenced["dates"] == plain["dates"]
    assert fenced["turnover"] < plain["turnover"]
    assert fenced["cost_per_period"] < plain["cost_per_period"]


def test_the_band_prices_alpha_from_trailing_ics_only():
    """The IC used to price alphas must come from earlier rebalances, never the sample it
    is applied to - an in-sample IC is look-ahead that always flatters and never shows."""
    con = _panel_db()
    r = band.run(con, "mom_12_1", 5, positions=10, banded=True, min_adv=0)
    # WARMUP rebalances are spent estimating and hold nothing, so they cannot score.
    total = band.panel(con, "mom_12_1", 5, min_adv=0)
    n_dates = len({x["date"] for x in total})
    assert r["dates"] == n_dates - band.WARMUP


def test_the_band_charges_forced_sales_it_did_not_choose():
    """A name leaving the eligible set is sold whether the rule likes it or not, and the
    cost is the rule's, not an exception to it."""
    con = _panel_db()
    r = band.run(con, "mom_12_1", 5, positions=10, banded=True, min_adv=0)
    assert all(s["cost"] >= 0 for s in r["series"])
    assert r["mean_excess"] < r["mean_excess_gross"]     # cost is never free


def test_no_positions_means_no_book():
    con = _panel_db(n_names=4)
    r = band.run(con, "mom_12_1", 5, positions=10, banded=True, min_adv=1e18)
    assert r["dates"] == 0
