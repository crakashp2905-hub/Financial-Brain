"""The walk-forward harness, and the three ways it could quietly cheat.

A forecast evaluation harness has exactly one job that cannot be got wrong: never let the forecaster
see the future. It could leak in three places - the history handed to the model, the session the
realised price is read from, and the universe the grid is drawn from - and a leak in any of them
produces a result that looks excellent and is worth nothing. Most of this file is about those three.

The rest is about comparability: a skill score computed against a null that was evaluated on a
different set of pairs is arithmetic on different data, and a t-statistic computed on overlapping
horizons is inflated by roughly sqrt(horizon / stride).
"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from financial_brain.forecasting import null
from financial_brain.forecasting import walkforward as W
from financial_brain.forecasting.distribution import ForecastDistribution, ForecastError

START = date(2020, 1, 1)


def _db(*, names=8, sessions=900, drift=0.0004):
    """Prices generated in SQL: 8 names x 900 sessions through executemany is ~800 rows/sec."""
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE adjusted_prices (business_date DATE, isin VARCHAR,
                   lineage VARCHAR, close_adj DOUBLE, turnover DOUBLE, volume BIGINT)""")
    con.execute("CREATE TABLE security_lineage (isin VARCHAR, lineage VARCHAR)")
    con.execute("""CREATE TABLE features (business_date DATE, lineage VARCHAR, adv20 DOUBLE)""")
    con.execute("""CREATE TABLE evaluation_runs (run_at TIMESTAMP, version VARCHAR,
                   feature VARCHAR, horizon INTEGER, params VARCHAR, dates INTEGER,
                   mean_ic DOUBLE, ic_t DOUBLE, sharpe DOUBLE, deflated_sharpe DOUBLE,
                   verdict VARCHAR, reasons VARCHAR, ic_series DOUBLE[], ic_dates DATE[])""")
    con.execute("""
        INSERT INTO adjusted_prices
        SELECT CAST(? AS DATE) + CAST(s.k AS INTEGER),
               'INE' || LPAD(CAST(n.i AS VARCHAR), 9, '0'),
               'L' || CAST(n.i AS VARCHAR),
               100.0 * POWER(1 + ? + 0.0001 * n.i, s.k)
                     * (1 + 0.02 * SIN(s.k * 0.7 + n.i)),
               1e9, 1000000
        FROM generate_series(0, ? - 1) AS s(k), generate_series(0, ? - 1) AS n(i)
    """, [START, drift, sessions, names])
    con.execute("""INSERT INTO security_lineage
                   SELECT DISTINCT isin, lineage FROM adjusted_prices""")
    con.execute("""INSERT INTO features
                   SELECT DISTINCT business_date, lineage, 1e9 FROM adjusted_prices""")
    return con


class _Peeker:
    """A forecaster that reports the last price it was given. Used to catch leaks."""

    name = "peeker"

    def __init__(self):
        self.seen: list[tuple[date, float, int]] = []

    def forecast(self, *, instrument, as_of, prices, horizon, n_paths=100):
        self.seen.append((as_of, prices[-1], len(prices)))
        return ForecastDistribution(
            instrument=instrument, as_of=as_of, horizon=horizon, anchor=prices[-1],
            paths=[[prices[-1]] * horizon for _ in range(n_paths)], model=self.name)


# ------------------------------------------------------------------ point-in-time discipline
def test_the_history_handed_to_a_forecaster_ends_at_the_forecast_date():
    """The leak that matters most, and the cheapest one to make: a history query without an upper
    bound hands the model the answer."""
    con = _db()
    sessions = [r[0] for r in con.execute(
        "SELECT DISTINCT business_date FROM adjusted_prices ORDER BY 1").fetchall()]
    as_of = sessions[500]
    prices = W.history_for(con, "L0", as_of)
    expected = [r[0] for r in con.execute(
        """SELECT close_adj FROM adjusted_prices WHERE lineage = ? AND business_date <= ?
           ORDER BY business_date DESC LIMIT ?""",
        ["L0", as_of, W.HISTORY]).fetchall()]
    assert prices == list(reversed(expected))
    assert prices[-1] == con.execute(
        "SELECT close_adj FROM adjusted_prices WHERE lineage = ? AND business_date = ?",
        ["L0", as_of]).fetchone()[0]


def test_the_walk_never_shows_a_forecaster_a_price_from_on_or_after_its_horizon():
    con = _db()
    peek = _Peeker()
    W.walk(con, peek, start=START, end=START + timedelta(days=880), horizon=20,
           names=4, n_paths=5, against_nulls=False)
    assert peek.seen
    for as_of, last_price, _ in peek.seen:
        after = con.execute(
            """SELECT COUNT(*) FROM adjusted_prices
               WHERE business_date > ? AND close_adj = ?""", [as_of, last_price]).fetchone()[0]
        # The anchor may legitimately recur later; what must not happen is the anchor being a price
        # that only exists after as_of.
        at_or_before = con.execute(
            """SELECT COUNT(*) FROM adjusted_prices
               WHERE business_date <= ? AND close_adj = ?""",
            [as_of, last_price]).fetchone()[0]
        assert at_or_before > 0, (
            f"the anchor {last_price} handed to the forecaster at {as_of} does not exist on or "
            f"before that session ({after} matches after it)")


def test_the_realised_price_is_counted_in_sessions_not_in_days():
    """A forecast scored 20 calendar days out is scored over a window whose length depends on where
    the holidays fell. Here the calendar is deliberately gapped."""
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE adjusted_prices (business_date DATE, isin VARCHAR,
                   lineage VARCHAR, close_adj DOUBLE, turnover DOUBLE, volume BIGINT)""")
    days = [START, START + timedelta(days=1), START + timedelta(days=40),
            START + timedelta(days=41), START + timedelta(days=90)]
    for k, d in enumerate(days):
        con.execute("INSERT INTO adjusted_prices VALUES (?,?,?,?,?,?)",
                    [d, "INE0", "L0", 100.0 + k, 1e9, 100])
    # Three sessions after the first is the fourth row, whatever the dates say.
    assert W.realised(con, "L0", days[0], 3) == 103.0
    assert W.realised(con, "L0", days[0], 4) == 104.0
    # And a horizon that runs off the end is None, not the last available price.
    assert W.realised(con, "L0", days[0], 5) is None


def test_a_horizon_running_past_the_data_is_dropped_rather_than_truncated():
    con = _db(sessions=300)
    peek = _Peeker()
    study = W.walk(con, peek, start=START, end=START + timedelta(days=299), horizon=20,
                   names=2, n_paths=5, against_nulls=False)
    assert study.skipped.get("no_realised_price", 0) > 0
    for fc, observed in study.records:
        assert W.realised(con, fc.instrument, fc.as_of, 20) == observed


def test_the_grid_universe_is_read_as_it_stood_on_each_session():
    """Selecting the universe once, at the end, is how a study becomes a study of what stayed
    liquid. Here one name loses its eligibility halfway through and must drop out from then on."""
    con = _db(names=6)
    sessions = [r[0] for r in con.execute(
        "SELECT DISTINCT business_date FROM adjusted_prices ORDER BY 1").fetchall()]
    cut = sessions[400]
    con.execute("UPDATE features SET adv20 = 1.0 WHERE lineage = 'L0' AND business_date >= ?",
                [cut])
    grid = W.sample_grid(con, start=START, end=sessions[-1], horizon=20, names=6,
                         min_adv=1e7)
    before = {i for d, i in grid if d < cut}
    after = {i for d, i in grid if d >= cut}
    assert "L0" in before
    assert "L0" not in after


# --------------------------------------------------------------------------- comparability
def test_every_null_is_scored_on_exactly_the_pairs_the_candidate_produced():
    con = _db()
    study = W.walk(con, null.Climatology(), start=START, end=START + timedelta(days=880),
                   horizon=20, names=4, n_paths=30)
    assert study.records
    for name, recs in study.null_records.items():
        assert len(recs) == len(study.records), f"{name} scored on a different sample"
        for (a, ya), (b, yb) in zip(study.records, recs):
            assert (a.instrument, a.as_of) == (b.instrument, b.as_of)
            assert ya == yb


def test_a_forecaster_that_declines_a_pair_drops_it_for_the_nulls_too():
    """Otherwise the skill score compares the candidate on easy pairs with the null on all of
    them."""
    class Fussy:
        name = "fussy"

        def forecast(self, *, instrument, as_of, prices, horizon, n_paths=100):
            if instrument.endswith("1"):
                raise ForecastError("not this one")
            return ForecastDistribution(
                instrument=instrument, as_of=as_of, horizon=horizon, anchor=prices[-1],
                paths=[[prices[-1]] * horizon for _ in range(n_paths)], model=self.name)

    con = _db(names=4)
    study = W.walk(con, Fussy(), start=START, end=START + timedelta(days=880),
                   horizon=20, names=4, n_paths=20)
    assert study.skipped.get("forecaster_declined", 0) > 0
    assert all(not fc.instrument.endswith("1") for fc, _ in study.records)
    for recs in study.null_records.values():
        assert len(recs) == len(study.records)
        assert all(not fc.instrument.endswith("1") for fc, _ in recs)


def test_overlapping_horizons_are_charged_for_rather_than_counted_as_independent():
    """The most common way a forecasting result is oversold. At stride == horizon nothing overlaps;
    at a quarter of the horizon each observation is worth a quarter of one."""
    con = _db()
    kw = dict(start=START, end=START + timedelta(days=880), horizon=20, names=4,
              n_paths=10, against_nulls=False)
    clean = W.walk(con, _Peeker(), stride=20, **kw)
    overlapping = W.walk(con, _Peeker(), stride=5, **kw)

    assert clean.independent_observations == pytest.approx(clean.n)
    assert overlapping.n > clean.n
    assert overlapping.independent_observations == pytest.approx(overlapping.n * 0.25)
    assert overlapping.independent_observations < clean.n * 1.2, (
        "sampling four times as often must not four times the independent sample")


def test_a_null_run_as_its_own_candidate_is_not_reported_as_losing_to_itself():
    con = _db()
    study = W.walk(con, null.Climatology(), start=START, end=START + timedelta(days=880),
                   horizon=20, names=4, n_paths=30)
    s = study.score()
    self_cmp = s["skill"]["climatology"]
    assert self_cmp["identical"] is True
    assert self_cmp["skill"] == pytest.approx(0.0)
    assert self_cmp["beats_null"] is None
    # And that vacuous comparison must not be what decides the verdict.
    assert isinstance(s["beats_every_null"], bool)


# ------------------------------------------------------------------------------- the ledger
def test_the_information_coefficient_uses_sessions_not_forecasts_for_its_t():
    """An IC series of 44 sessions has 44 observations however many names each one ranked. Dividing
    by the number of forecasts instead would inflate the t by sqrt(names)."""
    con = _db(names=8)
    study = W.walk(con, null.Climatology(), start=START, end=START + timedelta(days=880),
                   horizon=20, names=8, n_paths=30)
    ic = study.information_coefficient()
    assert ic["sessions"] < study.n, "there are more forecasts than sessions"
    assert len(ic["ic_series"]) == ic["sessions"]
    assert len(ic["ic_dates"]) == ic["sessions"]
    assert ic["ic_dates"] == sorted(ic["ic_dates"])


def test_an_ic_needs_a_cross_section_and_says_so_when_it_does_not_have_one():
    con = _db(names=2)
    study = W.walk(con, null.Climatology(), start=START, end=START + timedelta(days=880),
                   horizon=20, names=2, n_paths=20)
    ic = study.information_coefficient()
    assert ic["mean_ic"] is None
    assert "five names" in ic["why"]


def test_a_forecast_model_enters_the_ledger_as_a_trial_like_any_other_signal():
    """The integration that keeps this honest: a foundation model raises the Bonferroni bar for
    everything else in the ledger the moment it is tried."""
    con = _db(names=8)
    study = W.walk(con, null.Climatology(), start=START, end=START + timedelta(days=880),
                   horizon=20, names=8, n_paths=30)
    before = con.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0]
    out = W.record(con, study)
    after = con.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0]
    assert after == before + 1
    row = con.execute("""SELECT feature, horizon, version, verdict, ic_series
                         FROM evaluation_runs ORDER BY run_at DESC LIMIT 1""").fetchone()
    assert row[0] == "forecast:climatology"
    assert row[1] == 20
    assert row[2] == W.VERSION
    assert out["feature"] == row[0]
    assert row[4], "the IC series has to be stored, or the effective-trial count cannot be computed"


def test_a_study_that_beats_nothing_is_recorded_as_a_rejection_with_its_reasons():
    con = _db(names=8)
    study = W.walk(con, null.Drift(), start=START, end=START + timedelta(days=880),
                   horizon=20, names=8, n_paths=30)
    out = W.record(con, study)
    assert out["verdict"] in {"REJECT", "CANDIDATE"}
    if out["verdict"] == "REJECT":
        assert out["reasons"], "a rejection has to say what failed"


def test_a_study_with_no_records_refuses_to_score_rather_than_returning_zeroes():
    study = W.Study(model="nothing", horizon=20)
    with pytest.raises(ForecastError, match="recorded nothing"):
        study.score()
