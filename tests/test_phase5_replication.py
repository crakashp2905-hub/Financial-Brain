"""Replication across independent subsamples, which is the test that has beaten the overfitting here.

The module's claim is that an edge appearing with one sign in four tiers is far stronger evidence than
one clearing a high threshold in a single test. The tests check the three things that claim rests on:
that the tiers are built point-in-time and do not overlap, that the statistic is computed strictly
*inside* a tier so the size and market factors are differenced out, and that the joint probability is
the conjunction it claims to be rather than a restated single test.

A planted edge present in every tier must replicate; a planted edge present in only one must not, and
that second case is the one that matters - it is what every signal that died this session looked like.
"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import NormalDist

import duckdb
import pytest

from financial_brain.evaluation import replication as R

START = date(2018, 1, 1)
_N = NormalDist()


def _db(*, sessions=600, per_tier=60, edge_in=(), edge=0.0):
    """A market with four turnover tiers, and a planted edge in whichever tiers are named.

    Turnover is set so name index maps straight onto the tier boundaries: the first 50 by adv20 are
    `large`, the next 50 `next`, and so on. Each name carries a signal value, and in the tiers named
    by ``edge_in`` the forward return is that signal times ``edge`` - so the IC there is strongly
    positive and elsewhere it is noise.
    """
    names = 500
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE adjusted_prices (business_date DATE, lineage VARCHAR,
                   close_adj DOUBLE)""")
    con.execute("""CREATE TABLE features (business_date DATE, lineage VARCHAR,
                   adv20 DOUBLE, sig DOUBLE)""")
    con.execute("""CREATE TABLE candles (business_date DATE, lineage VARCHAR,
                   k_doji BOOLEAN)""")
    con.execute("CREATE TEMP TABLE edged (tier VARCHAR)")
    for t in edge_in:
        con.execute("INSERT INTO edged VALUES (?)", [t])

    cases = "\n".join(f"WHEN i < {hi} THEN '{name}'"
                      for name, _lo, hi, _a in R.TIERS)
    con.execute(f"""
        CREATE TEMP TABLE grid AS
        SELECT n.i AS i, s.k AS k,
               CAST(? AS DATE) + CAST(s.k AS INTEGER) AS business_date,
               'L' || CAST(n.i AS VARCHAR) AS lineage,
               -- adv20 descending in i, so i orders the tiers exactly.
               CAST({names} - n.i AS DOUBLE) * 1e6 AS adv20,
               -- A signal that varies within every tier and over time.
               SIN(n.i * 0.7 + s.k * 0.11) AS sig,
               CASE {cases} ELSE NULL END AS tier
        FROM generate_series(0, {sessions - 1}) AS s(k),
             generate_series(0, {names - 1}) AS n(i)
    """, [START])
    con.execute("INSERT INTO features SELECT business_date, lineage, adv20, sig FROM grid")
    con.execute("""INSERT INTO candles
                   SELECT business_date, lineage, (i % 8 = 0) FROM grid""")
    # The forward return: the signal times the edge where planted, plus a deterministic wiggle.
    con.execute(f"""
        INSERT INTO adjusted_prices
        SELECT business_date, lineage,
               100.0 * EXP(SUM(LN(1 + step)) OVER (PARTITION BY i ORDER BY k))
        FROM (SELECT g.*,
                     0.004 * SIN(g.i * 3.3 + g.k * 0.91)
                       + CASE WHEN e.tier IS NOT NULL THEN {edge} * g.sig ELSE 0.0 END AS step
              FROM grid g LEFT JOIN edged e ON e.tier = g.tier)
    """)
    return con


def _w(sessions=600):
    return {"start": START, "end": START + timedelta(days=sessions - 1)}


# --------------------------------------------------------------------------- the tiers
def test_the_tiers_do_not_overlap_and_cover_the_ranks_they_claim():
    seen = set()
    for name, lo, hi, approx in R.TIERS:
        assert lo <= hi
        band = set(range(lo, hi + 1))
        assert not (band & seen), f"{name} overlaps an earlier tier"
        seen |= band
        assert approx.startswith("~"), "each tier should name the index family it approximates"
    assert min(seen) == 1 and max(seen) == 500


def test_the_tiering_is_by_point_in_time_turnover_rank():
    """Not by membership of a real index: index_constituents is empty in this database. The ranking is
    recomputed per session from that session's adv20."""
    sql = R.tier_sql("sig", candle=False)
    assert "ROW_NUMBER() OVER (PARTITION BY f.business_date" in sql
    assert "ORDER BY f.adv20 DESC" in sql
    assert "index_constituents" not in sql


def test_the_rank_correlation_is_computed_inside_the_tier_and_session():
    """The whole independence argument. Ranks over the full universe would carry the size effect into
    every tier's IC, and a day when everything rose would contribute to all of them."""
    sql = R.tier_sql("sig", candle=False)
    assert "PARTITION BY tier, business_date ORDER BY x" in sql
    assert "PARTITION BY tier, business_date ORDER BY ret" in sql
    assert "GROUP BY tier, business_date" in sql


def test_a_candle_feature_is_read_from_the_candles_table():
    assert "FROM candles c" in R.tier_sql("k_doji", candle=True)
    assert "FROM features f\n" in R.tier_sql("sig", candle=False)


def test_ties_get_average_ranks_which_matters_for_boolean_features():
    sql = R.tier_sql("k_doji", candle=True)
    assert "COUNT(*) OVER (PARTITION BY tier, business_date, x) - 1) / 2.0" in sql


# ------------------------------------------------------------------ replication, both ways
def test_an_edge_present_in_every_tier_replicates():
    con = _db(edge_in=[t[0] for t in R.TIERS], edge=0.01)
    r = R.by_tier(con, feature="sig", horizon=20, **_w())
    assert all(t.ok() for t in r["tiers"]), [t.why for t in r["tiers"] if not t.ok()]
    v = r["verdict"]
    assert v["replicated"] is True
    assert v["signs_agree"] is True
    assert v["tiers_measured"] == 4
    assert v["joint_p"] < 1e-6


def test_an_edge_present_in_only_one_tier_does_not_replicate():
    """The case that matters. Every signal that died this session looked exactly like this in the one
    place it was measured."""
    con = _db(edge_in=["next"], edge=0.02)
    r = R.by_tier(con, feature="sig", horizon=20, **_w())
    v = r["verdict"]
    strong = [t for t in r["tiers"] if t.ok() and abs(t.t) >= R.TIER_T]
    assert any(t.tier == "next" for t in strong), "the planted tier should be strongly significant"
    assert v["replicated"] is False, (
        "an edge in one tier out of four is not a replication, whatever its t is there")


def test_the_weakest_tier_decides_not_the_average():
    """A mean over tiers lets one spectacular tier carry three empty ones. The conjunction is about
    the weakest link, so min|t| is the statistic."""
    con = _db(edge_in=["next"], edge=0.02)
    r = R.by_tier(con, feature="sig", horizon=20, **_w())
    usable = [t for t in r["tiers"] if t.ok()]
    v = r["verdict"]
    assert v["min_abs_t"] == pytest.approx(min(abs(t.t) for t in usable))
    assert v["max_abs_t"] > v["min_abs_t"]
    assert v["min_abs_t"] < R.TIER_T


def test_a_single_tier_cannot_support_a_replication_claim():
    con = _db(sessions=600, edge_in=["large"], edge=0.01)
    few = [t for t in R.by_tier(con, feature="sig", horizon=20, **_w())["tiers"]][:1]
    v = R.verdict(few)
    assert v["replicated"] is False
    assert "at least two tiers" in v["why"]


# ------------------------------------------------------------------------ the joint p
def test_the_joint_probability_is_the_conjunction_it_claims_to_be():
    """2 * Phi(-min|t|)^k, which is the whole argument for doing this rather than raising a single
    threshold."""
    from dataclasses import replace
    base = R.TierResult(tier="x", mean_ic=0.01, t=3.0, sessions=500,
                        names_per_session=50.0)
    for k in (2, 3, 4):
        tiers = [replace(base, tier=f"t{i}") for i in range(k)]
        v = R.verdict(tiers)
        assert v["joint_p"] == pytest.approx(2 * _N.cdf(-3.0) ** k)
        assert v["tiers_measured"] == k


def test_four_tiers_at_two_sigma_beat_a_single_test_at_the_bonferroni_bar():
    """The claim the module exists to make, asserted rather than asserted in prose."""
    from dataclasses import replace
    base = R.TierResult(tier="x", mean_ic=0.01, t=2.0, sessions=500,
                        names_per_session=50.0)
    four = R.verdict([replace(base, tier=f"t{i}") for i in range(4)])
    single_at_bar = 2 * _N.cdf(-3.72)
    assert four["joint_p"] < single_at_bar / 100, (
        f"four tiers at |t|=2 gives {four['joint_p']:.1e}, a single test at the bar gives "
        f"{single_at_bar:.1e}")


def test_disagreeing_signs_are_reported_as_such_and_never_replicate():
    from dataclasses import replace
    base = R.TierResult(tier="x", mean_ic=0.01, t=5.0, sessions=500,
                        names_per_session=50.0)
    tiers = [replace(base, tier="a"), replace(base, tier="b", t=-5.0)]
    v = R.verdict(tiers)
    assert v["signs_agree"] is False
    assert v["direction"] == 0
    assert v["replicated"] is False, "two strong tiers pointing opposite ways is noise, not an edge"


# ------------------------------------------------------------------------- the census
def test_the_census_charges_for_every_tier_trial_it_ran():
    con = _db(edge_in=[t[0] for t in R.TIERS], edge=0.01)
    con.execute("""CREATE TABLE evaluation_runs (run_at TIMESTAMP, version VARCHAR,
                   feature VARCHAR, horizon INTEGER, params VARCHAR, dates INTEGER,
                   mean_ic DOUBLE, ic_t DOUBLE, sharpe DOUBLE, deflated_sharpe DOUBLE,
                   verdict VARCHAR, reasons VARCHAR, ic_series DOUBLE[], ic_dates DATE[])""")
    c = R.census(con, features=("sig",), candles=("k_doji",), horizon=20, **_w())
    assert c["features_tried"] == 2
    assert c["trials_added"] >= 4
    assert c["single_test_bar_after"] > c["single_test_bar_before"]


def test_multiplicity_is_charged_by_features_tried_not_by_tiers():
    """The tiers are the conjunction and the conjunction has already paid for them. Charging them
    twice would make replication harder than a single test, which is backwards."""
    con = _db(edge_in=[t[0] for t in R.TIERS], edge=0.01)
    con.execute("""CREATE TABLE evaluation_runs (run_at TIMESTAMP, version VARCHAR,
                   feature VARCHAR, horizon INTEGER, params VARCHAR, dates INTEGER,
                   mean_ic DOUBLE, ic_t DOUBLE, sharpe DOUBLE, deflated_sharpe DOUBLE,
                   verdict VARCHAR, reasons VARCHAR, ic_series DOUBLE[], ic_dates DATE[])""")
    c = R.census(con, features=("sig",), candles=(), horizon=20, **_w())
    assert c["replicated"], "the planted edge should replicate"
    name, adj = c["survive_multiplicity"][0]
    joint = next(r["verdict"]["joint_p"] for r in c["results"] if r["feature"] == name)
    assert adj == pytest.approx(joint * c["features_tried"])
