"""Persistence and combination.

Two distinctions carry most of this file, because both have a cheaper wrong version that looks right.

A forecast whose horizon has not closed is not a forecast that failed to resolve. Both have a null
realised price, and collapsing them drops the most recent observations - which are the least
favourable ones, since a model gets deployed after a good backtest.

An ensemble mixes distributions; it does not average point forecasts. Two members predicting +10% and
-10% must give a wide distribution centred near zero, not a confident flat line, and the averaging
version produces exactly the confident flat line.
"""
from __future__ import annotations

from datetime import date

import duckdb
import pytest

from financial_brain.forecasting import ensemble as E
from financial_brain.forecasting import null, store
from financial_brain.forecasting.distribution import ForecastDistribution, ForecastError

AS_OF = date(2024, 6, 3)
START = date(2022, 1, 3)


def _db(*, sessions=400):
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE adjusted_prices (business_date DATE, lineage VARCHAR,
                   isin VARCHAR, close_adj DOUBLE)""")
    con.execute("""CREATE TABLE forecast_distributions (
                   forecast_id VARCHAR, lineage VARCHAR, as_of DATE, horizon INTEGER,
                   model VARCHAR, model_version VARCHAR, made_at TIMESTAMPTZ, anchor DOUBLE,
                   n_paths INTEGER, paths DOUBLE[][], config VARCHAR, data_version VARCHAR,
                   realised DOUBLE, realised_at DATE, scored_at TIMESTAMPTZ, crps DOUBLE,
                   pit DOUBLE)""")
    con.execute("""CREATE TABLE market_regime (business_date DATE, version VARCHAR,
                   regime VARCHAR)""")
    con.execute("""INSERT INTO adjusted_prices
        SELECT CAST(? AS DATE) + CAST(k AS INTEGER), 'L0', 'INE0',
               100.0 * POWER(1.0005, k)
        FROM generate_series(0, ? - 1) AS s(k)""", [START, sessions])
    return con


def _fc(model="m", *, paths=None, anchor=100.0, horizon=20, as_of=AS_OF, meta=None,
        instrument="L0"):
    paths = paths or [[anchor * (1 + 0.001 * i)] * horizon for i in range(50)]
    return ForecastDistribution(
        instrument=instrument, as_of=as_of, horizon=horizon, anchor=anchor, paths=paths,
        model=model, model_version=f"{model}-v1", meta=meta or {})


# ------------------------------------------------------------------------------ the store
def test_the_id_hashes_the_configuration_and_not_the_sampled_paths():
    """A stochastic model draws different paths each run. An id that moved with them would make every
    forecast unique and the table append-only by accident; one that ignored the configuration would
    let a forecast at a lucky temperature stand in for the record."""
    a = _fc(meta={"temperature": 1.0, "top_p": 0.9})
    b = _fc(meta={"temperature": 1.0, "top_p": 0.9},
            paths=[[101.0 + i] * 20 for i in range(50)])
    c = _fc(meta={"temperature": 0.5, "top_p": 0.9})
    assert store.forecast_id(a) == store.forecast_id(b), "paths must not enter the id"
    assert store.forecast_id(a) != store.forecast_id(c), "temperature must enter the id"


def test_saving_the_same_experiment_replaces_its_paths_rather_than_duplicating():
    con = _db()
    a = _fc(meta={"temperature": 1.0})
    fid = store.save(con, a)
    store.save(con, _fc(meta={"temperature": 1.0},
                        paths=[[123.0] * 20 for _ in range(50)]))
    assert con.execute("SELECT COUNT(*) FROM forecast_distributions").fetchone()[0] == 1
    assert store.load(con, fid).terminal()[0] == 123.0


def test_a_round_trip_preserves_the_paths_and_therefore_the_first_passage_answer():
    """The reason paths are stored at all: a quantile table cannot answer this question."""
    con = _db()
    detour = _fc(paths=[[94.0, 98.0, 104.0, 108.0]], horizon=4)
    fid = store.save(con, detour)
    back = store.load(con, fid)
    assert back.paths == detour.paths
    assert back.anchor == detour.anchor
    assert (back.first_passage(target=106.0, stop=95.0)
            == detour.first_passage(target=106.0, stop=95.0))
    assert back.first_passage(target=106.0, stop=95.0)["p_stop_first"] == 1.0


def test_a_forecast_whose_horizon_has_not_closed_is_pending_not_unresolvable():
    """The distinction that keeps a study from dropping its most recent observations."""
    con = _db(sessions=400)
    last = con.execute("SELECT MAX(business_date) FROM adjusted_prices").fetchone()[0]
    store.save(con, _fc(as_of=last, horizon=20))            # nothing after it yet
    r = store.resolve(con)
    assert r["pending"] == 1 and r["unresolvable"] == 0 and r["scored"] == 0


def test_a_forecast_whose_horizon_closed_without_a_price_is_unresolvable_not_pending():
    """Otherwise it waits forever. A lineage that stopped trading is information about the name."""
    con = _db(sessions=400)
    sessions = [r[0] for r in con.execute(
        "SELECT business_date FROM adjusted_prices ORDER BY 1").fetchall()]
    store.save(con, _fc(as_of=sessions[100], horizon=20, instrument="L_GONE"))
    r = store.resolve(con)
    assert r["unresolvable"] == 1 and r["pending"] == 0


def test_resolving_scores_a_closed_horizon_and_records_the_crps_and_pit():
    con = _db(sessions=400)
    sessions = [r[0] for r in con.execute(
        "SELECT business_date FROM adjusted_prices ORDER BY 1").fetchall()]
    as_of = sessions[100]
    anchor = con.execute("SELECT close_adj FROM adjusted_prices WHERE business_date = ?",
                         [as_of]).fetchone()[0]
    store.save(con, _fc(as_of=as_of, horizon=20, anchor=anchor))
    r = store.resolve(con)
    assert r["scored"] == 1
    row = con.execute("""SELECT realised, crps, pit, scored_at FROM forecast_distributions
                         LIMIT 1""").fetchone()
    expected = con.execute("SELECT close_adj FROM adjusted_prices WHERE business_date = ?",
                           [sessions[120]]).fetchone()[0]
    assert row[0] == pytest.approx(expected), "realised must be 20 SESSIONS on, not 20 days"
    assert row[1] is not None and row[1] >= 0
    assert 0.0 < row[2] < 1.0
    assert row[3] is not None


def test_resolving_is_idempotent():
    con = _db(sessions=400)
    sessions = [r[0] for r in con.execute(
        "SELECT business_date FROM adjusted_prices ORDER BY 1").fetchall()]
    store.save(con, _fc(as_of=sessions[100], horizon=20))
    first = store.resolve(con)
    second = store.resolve(con)
    assert first["scored"] == 1
    assert second["considered"] == 0 and second["scored"] == 0


def test_the_aggregate_reports_calibration_from_the_stored_pits():
    con = _db(sessions=400)
    sessions = [r[0] for r in con.execute(
        "SELECT business_date FROM adjusted_prices ORDER BY 1").fetchall()]
    for k in range(60):
        as_of = sessions[150 + k]
        anchor = con.execute(
            "SELECT close_adj FROM adjusted_prices WHERE business_date = ?",
            [as_of]).fetchone()[0]
        fc = null.Climatology(seed=k).forecast(
            instrument="L0", as_of=as_of, horizon=20,
            prices=[r[0] for r in con.execute(
                "SELECT close_adj FROM adjusted_prices WHERE business_date <= ? ORDER BY 1",
                [as_of]).fetchall()], n_paths=60)
        assert fc.anchor == pytest.approx(anchor)
        store.save(con, fc)
    store.resolve(con)
    agg = store.scored(con, model="climatology", horizon=20)
    assert agg["n"] == 60
    assert agg["crps"] is not None and agg["crps"] > 0
    assert agg["sessions"] == 60
    assert agg["calibration"]["n"] == 60


# --------------------------------------------------------------------------- the ensemble
def test_an_unmeasured_member_gets_no_weight_rather_than_a_default_share():
    """A weight on an unknown skill is an unmeasured bet."""
    ms = [E.Member("a", None, crps=2.0, n_scored=100),
          E.Member("b", None, crps=4.0, n_scored=100),
          E.Member("untested", None)]
    w = E.weights(ms)
    assert set(w) == {"a", "b"}
    assert sum(w.values()) == pytest.approx(1.0)


def test_with_nothing_measured_the_pool_refuses_rather_than_weighting_equally():
    with pytest.raises(ForecastError, match="measured CRPS"):
        E.weights([E.Member("a", None), E.Member("b", None)])


def test_the_better_member_gets_more_weight_and_shrinkage_pulls_it_toward_equal():
    ms = [E.Member("good", None, crps=1.0, n_scored=100),
          E.Member("bad", None, crps=9.0, n_scored=100)]
    raw = E.weights(ms, shrinkage=1.0)
    half = E.weights(ms, shrinkage=0.5)
    flat = E.weights(ms, shrinkage=0.0)
    assert raw["good"] > half["good"] > flat["good"] == pytest.approx(0.5)
    assert raw["good"] == pytest.approx(0.9)
    for w in (raw, half, flat):
        assert sum(w.values()) == pytest.approx(1.0)


def test_a_pool_of_disagreeing_members_is_wide_rather_than_confidently_neutral():
    """The whole reason paths are mixed instead of averaged. Averaging +10% and -10% gives a flat
    line at zero with no spread; mixing gives a bimodal distribution that shows the disagreement."""
    up = _fc("up", paths=[[110.0] * 20 for _ in range(100)])
    down = _fc("down", paths=[[90.0] * 20 for _ in range(100)])
    w = {"up": 0.5, "down": 0.5}
    mixed = E.pool({"up": up, "down": down}, w, n_paths=400, seed=1)

    assert mixed.expected_return() == pytest.approx(0.0, abs=0.05)
    terminals = set(mixed.terminal())
    assert terminals == {110.0, 90.0}, "the mixture keeps both members' outcomes"
    q = mixed.quantiles()
    assert q["p05"] == 90.0 and q["p95"] == 110.0, (
        "an averaged forecast would have zero interval width here")
    assert mixed.probability_above(100.0) == pytest.approx(0.5, abs=0.1)


def test_a_pool_is_at_least_as_wide_as_its_members_which_is_correct_not_a_bug():
    """A linear pool is reliably under-confident (Hora 2004), and under-confidence is the safe
    direction: an ensemble that overstates its precision sizes positions too large."""
    import random

    rng = random.Random(3)
    a = _fc("a", paths=[[rng.gauss(102.0, 2.0)] * 20 for _ in range(200)])
    b = _fc("b", paths=[[rng.gauss(98.0, 2.0)] * 20 for _ in range(200)])
    mixed = E.pool({"a": a, "b": b}, {"a": 0.5, "b": 0.5}, n_paths=800, seed=2)

    from financial_brain.forecasting import calibration as C
    assert C.sharpness(mixed.terminal()) > C.sharpness(a.terminal())
    assert C.sharpness(mixed.terminal()) > C.sharpness(b.terminal())


def test_pooling_across_different_anchors_is_refused():
    """The result would look entirely reasonable while combining forecasts of different quantities."""
    a = _fc("a", anchor=100.0)
    b = _fc("b", anchor=250.0)
    with pytest.raises(ForecastError, match="anchor"):
        E.pool({"a": a, "b": b}, {"a": 0.5, "b": 0.5})


def test_pooling_across_different_horizons_is_refused():
    a = _fc("a", horizon=20)
    b = _fc("b", horizon=40, paths=[[100.0] * 40 for _ in range(50)])
    with pytest.raises(ForecastError, match="h20|h40"):
        E.pool({"a": a, "b": b}, {"a": 0.5, "b": 0.5})


def test_the_pool_records_the_weights_it_actually_used():
    a = _fc("a")
    b = _fc("b")
    mixed = E.pool({"a": a, "b": b}, {"a": 0.75, "b": 0.25}, n_paths=200, seed=4)
    assert mixed.meta["weights"] == {"a": 0.75, "b": 0.25}
    assert mixed.model == "ensemble"
    assert mixed.model_version == "a+b"


def test_the_pool_is_reproducible_from_its_seed():
    a = _fc("a", paths=[[100.0 + i] * 20 for i in range(50)])
    b = _fc("b", paths=[[200.0 + i] * 20 for i in range(50)])
    w = {"a": 0.5, "b": 0.5}
    x = E.pool({"a": a, "b": b}, w, n_paths=100, seed=9)
    y = E.pool({"a": a, "b": b}, w, n_paths=100, seed=9)
    assert x.paths == y.paths


# ------------------------------------------------------------------------ the regime router
def _router():
    return E.RegimeRouter(pooled=[E.Member("a", None, crps=2.0, n_scored=200),
                                  E.Member("b", None, crps=3.0, n_scored=200)])


def test_the_router_refuses_to_route_a_regime_it_has_barely_seen():
    """Routing across five regimes needs five weight vectors from data that struggled to fit one.
    A regime with eight observations produces a weight vector fitted to eight numbers."""
    r = _router()
    r.by_regime = {"CRISIS": {"a": 1.0, "b": 9.0}}
    r.counts = {"CRISIS": 8}
    out = r.route("CRISIS")
    assert out["routed"] is False
    assert "below" in out["why"]
    # And it falls back to the pooled weights rather than to the eight-observation fit.
    assert out["weights"] == E.weights(r.pooled)


def test_the_router_routes_once_a_regime_has_enough_of_its_own_observations():
    r = _router()
    r.by_regime = {"RISK_ON": {"a": 9.0, "b": 1.0}}
    r.counts = {"RISK_ON": E.MIN_PER_REGIME + 10}
    out = r.route("RISK_ON")
    assert out["routed"] is True
    assert out["weights"]["b"] > out["weights"]["a"], (
        "inside this regime b has the better CRPS and must get the larger weight")
    assert out["regime_version"] == "v2"


def test_an_unclassified_session_does_not_route_into_whichever_bucket_is_called_normal():
    r = _router()
    out = r.route(None)
    assert out["routed"] is False
    assert out["weights"] == E.weights(r.pooled)


def test_fitting_records_which_regimes_are_routable_and_which_are_only_seen():
    r = _router()
    records = []
    for _i in range(E.MIN_PER_REGIME + 5):
        records.append((_fc("a", paths=[[101.0 + j] * 20 for j in range(10)]), 105.0,
                        "RISK_ON"))
        records.append((_fc("b", paths=[[99.0 + j] * 20 for j in range(10)]), 105.0,
                        "RISK_ON"))
    for _i in range(5):
        records.append((_fc("a", paths=[[101.0 + j] * 20 for j in range(10)]), 90.0,
                        "CRISIS"))
        records.append((_fc("b", paths=[[99.0 + j] * 20 for j in range(10)]), 90.0,
                        "CRISIS"))
    out = r.fit(records)
    assert set(out["regimes"]) == {"RISK_ON", "CRISIS"}
    assert out["routable"] == ["RISK_ON"]
    assert r.route("RISK_ON")["routed"] is True
    assert r.route("CRISIS")["routed"] is False


def test_the_regime_lookup_returns_none_for_an_unclassified_session():
    con = _db()
    con.execute("INSERT INTO market_regime VALUES (?, 'v2', 'RISK_ON')",
                [date(2023, 1, 5)])
    assert E.regime_at(con, date(2023, 6, 1)) == "RISK_ON"
    assert E.regime_at(con, date(2022, 1, 1)) is None, (
        "a missing regime must not read as a default label")


def test_the_regime_version_is_honoured_because_v1_and_v2_disagree():
    """They disagree on 14.7% of sessions, so weights fitted under one definition do not transfer."""
    con = _db()
    con.execute("INSERT INTO market_regime VALUES (?, 'v1', 'RISK_OFF')", [date(2023, 1, 5)])
    con.execute("INSERT INTO market_regime VALUES (?, 'v2', 'RISK_ON')", [date(2023, 1, 5)])
    assert E.regime_at(con, date(2023, 6, 1), version="v1") == "RISK_OFF"
    assert E.regime_at(con, date(2023, 6, 1), version="v2") == "RISK_ON"
