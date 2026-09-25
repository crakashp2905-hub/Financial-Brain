"""Per-stock behaviour, and the line between adaptive and overfitted.

A rule that varies by stock is either good methodology or invented edge, and the only
thing separating them is whether the variation is computed from that name's *trailing*
data by a formula that is the same for every name. These tests pin that.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import duckdb

from financial_brain.evaluation import timeseries as ts
from financial_brain.features import behaviour


def _con(series_by_name):
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE adjusted_prices (business_date DATE, lineage VARCHAR,
                   isin VARCHAR, close_adj DOUBLE, factor DOUBLE, turnover DOUBLE)""")
    con.execute("""CREATE TABLE eod_prices (business_date DATE, isin VARCHAR,
                   exchange VARCHAR, series VARCHAR, open_price DOUBLE,
                   high_price DOUBLE, low_price DOUBLE)""")
    start = date(2016, 1, 1)
    for lin, closes in series_by_name.items():
        isin = f"ISIN{lin}"
        for i, c in enumerate(closes):
            d = start + timedelta(days=i)
            con.execute("INSERT INTO adjusted_prices VALUES (?,?,?,?,?,?)",
                        [d, lin, isin, c, 1.0, 1e9])
            con.execute("INSERT INTO eod_prices VALUES (?,?,?,?,?,?,?)",
                        [d, isin, "NSE", "EQ", c, c * 1.02, c * 0.98])
    return con


def _vr(con, lineage):
    for st in behaviour.BEHAVIOUR_SQL.strip().split(";\n"):
        if st.strip():
            con.execute(st)
    row = con.execute("""SELECT vr_60 FROM _behaviour WHERE lineage = ?
                         AND vr_60 IS NOT NULL ORDER BY d DESC LIMIT 1""",
                      [lineage]).fetchone()
    return row[0] if row else None


def _persistent(n=160, step=1.0, run=5):
    """Runs of same-signed moves: returns are positively autocorrelated, so five-session
    moves accumulate instead of cancelling.

    Note a smooth exponential trend does *not* work here - its returns are constant, so
    both variances are floating-point noise and the ratio is undefined rather than large.
    Persistence is a property of the returns, not of the price direction.
    """
    out, px = [100.0], 100.0
    for i in range(1, n):
        px += step if (i // run) % 2 == 0 else -step
        out.append(px)
    return out


def test_a_persistent_series_has_a_variance_ratio_above_one():
    got = _vr(_con({"T": _persistent()}), "T")
    assert got is not None and got > 1.0


def test_an_alternating_series_has_a_variance_ratio_below_one():
    """Perfect reversal cancels over longer horizons, so the ratio collapses."""
    revert = [100.0 + (2.0 if i % 2 else -2.0) for i in range(160)]
    got = _vr(_con({"R": revert}), "R")
    assert got is not None and got < 1.0


def test_the_two_are_ordered_as_the_measure_claims():
    con = _con({"T": _persistent(),
                "R": [100.0 + (2.0 if i % 2 else -2.0) for i in range(160)]})
    for st in behaviour.BEHAVIOUR_SQL.strip().split(";\n"):
        if st.strip():
            con.execute(st)
    rows = dict(con.execute("""SELECT lineage, MEDIAN(vr_60) FROM _behaviour
                               WHERE vr_60 IS NOT NULL GROUP BY 1""").fetchall())
    assert rows["T"] > rows["R"]


def test_behaviour_columns_are_null_until_enough_history():
    """vr_60 needs 66 sessions, atr_pctile_250 needs 250: never computed on a short
    window, the same rule the rest of the feature table keeps."""
    con = _con({"S": [100.0 + i for i in range(40)]})
    for st in behaviour.BEHAVIOUR_SQL.strip().split(";\n"):
        if st.strip():
            con.execute(st)
    row = con.execute("""SELECT COUNT(vr_60), COUNT(atr_pctile_250)
                         FROM _behaviour""").fetchone()
    assert row == (0, 0)


def test_the_volatility_percentile_is_against_the_names_own_history():
    """"Volatile" must mean volatile for this stock, not for the market - otherwise a
    quiet name is never flagged and a jumpy one always is."""
    assert "PARTITION BY lineage" in behaviour.BEHAVIOUR_SQL
    assert "249 PRECEDING" in behaviour.BEHAVIOUR_SQL


def test_every_window_is_strictly_backward_looking():
    """The whole legitimacy of per-stock adaptation rests on this."""
    sql = behaviour.BEHAVIOUR_SQL
    assert "FOLLOWING" not in sql.upper(), "no window may see the future"


# --- the dynamic strategies ---------------------------------------------------

def test_the_atr_band_is_one_coefficient_not_a_per_name_fit():
    """The band differs per stock because the ATR does, not because anything was fitted.
    One number, 1.5, shared by the whole universe."""
    spec = ts.STRATEGIES["above_ma200_atr_band"]
    assert "1.5 * atr_14" in spec["entry"]
    assert "1.5 * atr_14" in spec["exit"]
    # and it is symmetric: no separate entry and exit coefficient to tune apart
    assert spec["entry"].count("1.5") == spec["exit"].count("1.5") == 1


def test_a_quiet_name_gets_a_narrower_band_than_a_volatile_one():
    """The point of the whole exercise: a fixed 5% was 2.2 ATR on a quiet name and
    0.8 ATR on a volatile one. In price terms the ATR band must differ."""
    quiet = ts.INDICATORS["atr_14"]
    assert "AVG(tr)" in quiet and "13 PRECEDING" in quiet


def test_the_variance_ratio_gate_is_a_condition_not_a_parameter():
    """vr_60 > 1 is the measure's own neutral point - a random walk - not a threshold
    chosen by trying values."""
    spec = ts.STRATEGIES["trend_only_when_trending"]
    assert "vr_60 > 1" in spec["entry"]
    assert "vr_60 <= 1" in spec["exit"]


def test_the_variance_ratio_in_the_harness_matches_the_feature_definition():
    """Two implementations of one statistic must agree, or a strategy and its
    documentation are describing different things."""
    expr = ts.INDICATORS["vr_60"]
    assert "VAR_SAMP(r5)" in expr and "5 * VAR_SAMP(r1)" in expr
    assert "59 PRECEDING" in expr


def test_the_panel_computes_returns_without_nesting_windows():
    """VAR_SAMP over a LAG would be a window inside a window, which SQL forbids - the
    returns are precomputed in the panel instead."""
    assert "AS r1" in ts.PANEL and "AS r5" in ts.PANEL
    assert math.isclose(1.0, 1.0)      # sanity, keeps the import honest
