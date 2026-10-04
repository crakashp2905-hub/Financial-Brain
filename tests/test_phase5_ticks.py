from datetime import datetime, timedelta, timezone

import duckdb
import pytest

from financial_brain.ingest import ticks as T

IST = timezone(timedelta(hours=5, minutes=30))


def at(h, m, s=0):
    return datetime(2026, 9, 25, h, m, s, tzinfo=IST)


def test_bar_is_released_only_when_the_next_minute_starts():
    a = T.Aggregator()
    assert a.feed(token=1, ts=at(9, 15, 1), price=100, cum_volume=1000) == []
    assert a.feed(token=1, ts=at(9, 15, 40), price=101, cum_volume=1500) == []
    out = a.feed(token=1, ts=at(9, 16, 2), price=100.5, cum_volume=1800)
    assert len(out) == 1 and out[0]["ts"] == at(9, 15)
    assert (out[0]["open"], out[0]["high"], out[0]["low"], out[0]["close"]) == (100, 101, 100, 101)


def test_volume_is_the_difference_of_cumulative_not_a_sum():
    a = T.Aggregator()
    bars = []
    for ts, cv in ((at(9, 15), 1000), (at(9, 16), 1400), (at(9, 16, 30), 1900), (at(9, 17), 2000)):
        bars += a.feed(token=1, ts=ts, price=100, cum_volume=cv)
    bars += a.flush()
    by = {b["ts"]: b["volume"] for b in bars}
    assert by[at(9, 16)] == 1900 - 1000          # 900 shares in the 09:16 minute, not 1400+1900


def test_first_bar_has_unknown_volume_not_the_days_total():
    a = T.Aggregator()
    a.feed(token=1, ts=at(10, 0), price=100, cum_volume=5_000_000)
    assert a.flush()[0]["volume"] == 0


def test_late_tick_is_dropped_and_counted():
    a = T.Aggregator()
    a.feed(token=1, ts=at(9, 16), price=100, cum_volume=10)
    a.feed(token=1, ts=at(9, 15, 59), price=99, cum_volume=9)
    assert a.dropped_late == 1 and a.flush()[0]["low"] == 100


def test_instruments_are_independent():
    a = T.Aggregator()
    a.feed(token=1, ts=at(9, 15), price=100, cum_volume=1)
    a.feed(token=2, ts=at(9, 15), price=50, cum_volume=1)
    assert {b["instrument_token"] for b in a.flush()} == {1, 2}


def test_bad_ticks_are_refused():
    with pytest.raises(T.TickError):
        T.Aggregator().feed(token=1, ts=at(9, 15), price=0, cum_volume=1)


def test_store_never_overwrites_a_historical_bar():
    c = duckdb.connect()
    c.execute("""CREATE TABLE minute_bars(instrument_token BIGINT, tradingsymbol VARCHAR, isin VARCHAR,
                 ts TIMESTAMPTZ, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT,
                 source VARCHAR)""")
    c.execute("INSERT INTO minute_bars VALUES (1,'X','I',?,1,1,1,1,10,'KITE')", [at(9, 15)])
    bar = {"instrument_token": 1, "ts": at(9, 15), "open": 9, "high": 9, "low": 9, "close": 9,
           "volume": 9, "source": T.SOURCE}
    assert T.store(c, [bar]) == 0
    assert c.execute("SELECT close FROM minute_bars").fetchone()[0] == 1
