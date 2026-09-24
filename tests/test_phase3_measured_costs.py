"""The measured cost model: an estimator that failed, and the bracket that replaced it.

The impact term is 71% of a mid-cap round trip and decided fifty-six rejections. These
tests pin what was learned trying to measure it, because the failure is the useful part:
a well-known estimator produced a confident, wrong number, and only a plausibility check
caught it.
"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from financial_brain.costs import measured
from financial_brain.costs.india import DEFAULT_IMPACT


def _con(n_names=20, n_days=120, price=500.0, rng=0.04):
    """A toy panel with a known daily range and no spread at all."""
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE adjusted_prices (business_date DATE, lineage VARCHAR,
                   isin VARCHAR, close_adj DOUBLE, factor DOUBLE, turnover DOUBLE)""")
    con.execute("""CREATE TABLE eod_prices (business_date DATE, isin VARCHAR,
                   exchange VARCHAR, series VARCHAR, open_price DOUBLE,
                   high_price DOUBLE, low_price DOUBLE)""")
    start = date(2016, 1, 1)
    for i in range(n_names):
        lin, isin = f"L{i}", f"ISIN{i}"
        for k in range(n_days):
            d = start + timedelta(days=k)
            c = price * (1 + 0.001 * ((i + k) % 7 - 3))
            con.execute("INSERT INTO adjusted_prices VALUES (?,?,?,?,?,?)",
                        [d, lin, isin, c, 1.0, 1e9 / (i + 1)])
            con.execute("INSERT INTO eod_prices VALUES (?,?,?,?,?,?,?)",
                        [d, isin, "NSE", "EQ", c, c * (1 + rng / 2), c * (1 - rng / 2)])
    return con


def test_negative_alphas_are_clamped_to_zero_not_dropped():
    """Corwin & Schultz's own prescription, and the bug this module shipped with.

    34.2% of alphas on the real panel are negative, so the estimator is mostly noise
    around a small true spread. Discarding the negative half and taking the median of the
    survivors returns roughly the 75th percentile of that noise - it produced a 53.8 bps
    half-spread for the most liquid 5% of Indian equities, where one tick is 0.95 bps.
    """
    assert "ELSE 0 END AS spread" in measured.SPREAD_SQL
    assert "CASE WHEN a > 0" in measured.SPREAD_SQL


def test_the_spread_estimator_is_averaged_not_medianed():
    """With about a third of the sample clamped to zero, those zeros carry information
    about how small the true spread is, and a median would discard it."""
    import inspect
    assert "AVG(spread)" in inspect.getsource(measured.estimate)
    assert "MEDIAN(spread)" not in inspect.getsource(measured.estimate)


def test_the_bracket_never_gates_on_the_optimistic_bound():
    """Nothing may be promoted on a hopeful cost assumption: the pessimistic bound is
    always at least the optimistic one, whatever the estimators say."""
    con = _con()
    br = measured.bracket(con)
    assert br
    for bucket, v in br.items():
        assert v["pessimistic"] >= v["optimistic"], bucket


def test_the_optimistic_bound_is_at_least_one_tick():
    """A hard floor: no fill crosses for less than the minimum price increment."""
    con = _con(price=500.0)
    br = measured.bracket(con)
    for bucket, v in br.items():
        assert v["tick_bps"] > 0, bucket
        assert v["optimistic_bps"] >= v["tick_bps"], bucket


def test_a_cheaper_tick_makes_the_optimistic_bound_cheaper():
    """The floor tracks price: a tick is a larger fraction of a cheap share."""
    dear = measured.bracket(_con(price=2000.0))
    cheap = measured.bracket(_con(price=50.0))
    common = set(dear) & set(cheap)
    assert common
    for b in common:
        assert cheap[b]["tick_bps"] > dear[b]["tick_bps"], b


def test_round_trip_keeps_the_statutory_terms_exact():
    """STT is a published rate and is not estimated: 20 bps over both legs, always."""
    assert measured.round_trip_bps(0.0) == pytest.approx(20.9, abs=0.01)
    # and impact enters twice, once per leg
    assert measured.round_trip_bps(0.001) == pytest.approx(20.9 + 20.0, abs=0.01)


def test_the_assumed_table_is_still_reachable_as_the_pessimistic_bound():
    """The original constants are not deleted. They became the worst case, which is the
    only honest way to revise a number that decides outcomes."""
    con = _con()
    br = measured.bracket(con)
    for bucket, v in br.items():
        assert v["pessimistic_bps"] >= DEFAULT_IMPACT[bucket] * 10_000 - 1e-6, bucket
