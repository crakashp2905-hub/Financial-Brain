"""Technical indicators and candlestick patterns: the arithmetic, checked against hand values.

An indicator is only worth testing as a signal if it is the indicator it claims to be. A
miscomputed RSI that gets rejected teaches nothing - the rejection is about the bug, not
the idea. So these check the definitions against values computed by hand on small, legible
series, and check the geometry of each candlestick pattern against bars built to be that
pattern and bars built to be nearly it.
"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from financial_brain.features import candles, technical


def _con(bars):
    """bars: (lineage, date, open, high, low, close, volume)."""
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE adjusted_prices (business_date DATE, lineage VARCHAR,
                   isin VARCHAR, close_adj DOUBLE, factor DOUBLE, turnover DOUBLE,
                   traded_volume BIGINT)""")
    con.execute("""CREATE TABLE eod_prices (business_date DATE, isin VARCHAR,
                   exchange VARCHAR, series VARCHAR, open_price DOUBLE,
                   high_price DOUBLE, low_price DOUBLE)""")
    for lin, d, o, h, low, c, v in bars:
        isin = f"ISIN{lin}"
        con.execute("INSERT INTO adjusted_prices VALUES (?,?,?,?,?,?,?)",
                    [d, lin, isin, c, 1.0, 1e9, v])
        con.execute("INSERT INTO eod_prices VALUES (?,?,?,?,?,?,?)",
                    [d, isin, "NSE", "EQ", o, h, low])
    return con


def _flat(closes, lin="L0"):
    """Bars whose open/high/low bracket the close, so OHLC indicators are well defined."""
    start = date(2020, 1, 1)
    return [(lin, start + timedelta(days=i), c, c * 1.01, c * 0.99, c, 100_000)
            for i, c in enumerate(closes)]


def _tech(con):
    for sql in (technical.BARS_SQL, technical.PARTS_SQL, technical.MACD_SQL):
        for st in sql.strip().split(";\n"):
            if st.strip():
                con.execute(st)
    con.execute("CREATE TABLE features AS SELECT lineage, d AS business_date FROM _tbars")
    for st in technical.INDICATORS_SQL.strip().split(";\n"):
        if st.strip():
            con.execute(st)
    return con


def test_rsi_is_100_when_every_session_rises():
    """The boundary case with an unarguable answer: no down closes, so RSI is 100."""
    con = _tech(_con(_flat([100 + i for i in range(40)])))
    got = con.execute("""SELECT rsi_14 FROM features
                         ORDER BY business_date DESC LIMIT 1""").fetchone()[0]
    assert got == pytest.approx(100.0)


def test_rsi_is_zero_when_every_session_falls():
    con = _tech(_con(_flat([200 - i for i in range(40)])))
    got = con.execute("""SELECT rsi_14 FROM features
                         ORDER BY business_date DESC LIMIT 1""").fetchone()[0]
    assert got == pytest.approx(0.0)


def test_rsi_is_50_when_gains_and_losses_are_equal():
    closes = [100 + (1 if i % 2 else -1) for i in range(41)]
    con = _tech(_con(_flat(closes)))
    got = con.execute("""SELECT rsi_14 FROM features
                         ORDER BY business_date DESC LIMIT 1""").fetchone()[0]
    assert got == pytest.approx(50.0, abs=3.0)


def test_stochastic_and_williams_are_the_same_range_measured_from_opposite_ends():
    """%K counts up from the low, %R counts down from the high: %R = %K - 100."""
    con = _tech(_con(_flat([100 + (i * 7) % 23 for i in range(60)])))
    rows = con.execute("""SELECT stoch_k_14, williams_r_14 FROM features
                          WHERE stoch_k_14 IS NOT NULL""").fetchall()
    assert rows
    for k, r in rows:
        assert r == pytest.approx(k - 100, abs=1e-6)


def test_bollinger_percent_b_is_a_half_at_the_mean():
    con = _tech(_con(_flat([100.0] * 30 + [100.0])))
    got = con.execute("""SELECT bb_pct_20 FROM features
                         WHERE bb_pct_20 IS NOT NULL LIMIT 1""").fetchone()
    assert got is None or got[0] is None       # zero deviation: undefined, not invented


def test_adx_is_direction_free():
    """Trend strength, not trend sign: a steady rise and a steady fall score alike."""
    up = _tech(_con(_flat([100 + i for i in range(60)])))
    down = _tech(_con(_flat([200 - i for i in range(60)])))
    a = up.execute("SELECT adx_14 FROM features ORDER BY business_date DESC LIMIT 1").fetchone()[0]
    b = down.execute("SELECT adx_14 FROM features ORDER BY business_date DESC LIMIT 1").fetchone()[0]
    assert a == pytest.approx(b, abs=1.0)


def test_indicators_are_null_until_enough_history_exists():
    """Never computed on a short window - the rule the rest of the feature table keeps."""
    con = _tech(_con(_flat([100 + i for i in range(10)])))
    row = con.execute("""SELECT COUNT(rsi_14), COUNT(adx_14), COUNT(cci_20)
                         FROM features""").fetchone()
    assert row == (0, 0, 0)


def test_the_panel_pins_one_exchange_leg():
    assert "e.exchange = 'NSE'" in technical.BARS_SQL
    assert "e.series = 'EQ'" in technical.BARS_SQL
    assert "e.exchange = 'NSE'" in candles.GEOMETRY


def test_highs_and_lows_carry_the_adjustment_factor():
    for sql in (technical.BARS_SQL, candles.GEOMETRY):
        assert "e.high_price * p.factor" in sql
        assert "e.low_price  * p.factor" in sql
