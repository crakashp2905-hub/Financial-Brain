from datetime import datetime, timedelta, timezone

import duckdb
import pytest

from financial_brain.features import intraday as I

IST = timezone(timedelta(hours=5, minutes=30))


def minute_bars(con, tok, day, base, drift=0.0, vol=1000, n=375):
    t0 = datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST)
    rows = []
    for k in range(n):
        p = base * (1 + drift * k)
        rows.append((tok, "T", "ISIN", t0 + timedelta(minutes=k), p, p * 1.001, p * 0.999, p, vol))
    con.executemany("INSERT INTO minute_bars VALUES (?,?,?,?,?,?,?,?,?)", rows)


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")           # the trap: a UTC connection must not shift the minutes
    c.execute("""CREATE TABLE minute_bars(instrument_token BIGINT, tradingsymbol VARCHAR, isin VARCHAR,
                 ts TIMESTAMPTZ, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT)""")
    from datetime import date
    minute_bars(c, 1, date(2026, 1, 5), 100.0)
    minute_bars(c, 1, date(2026, 1, 6), 102.0, drift=0.0002)       # gaps up, then drifts up
    minute_bars(c, 1, date(2026, 1, 7), 103.0)
    return c


def test_row_per_session_and_first_session_dropped_for_want_of_a_previous_close(con):
    assert I.build(con) == 2
    assert con.execute("SELECT MIN(session) FROM intraday_features").fetchone()[0].isoformat() == "2026-01-06"


def test_gap_uses_previous_close_and_minutes_are_in_ist_not_utc(con):
    I.build(con)
    gap, brk, r = con.execute("""SELECT gap, or_breakout, r_to_close FROM intraday_features
                                 WHERE session = DATE '2026-01-06'""").fetchone()
    assert gap == pytest.approx(102.0 / 100.0 - 1, rel=0.02)
    assert brk == 1                        # a rising session is above its 09:15-09:30 high at 10:30
    assert r > 0                           # which only works if 10:30 is 10:30 and not 05:00


def test_outcome_starts_at_the_feature_minute_not_the_open(con):
    I.build(con)
    r, nxt = con.execute("""SELECT r_to_close, r_to_next_close FROM intraday_features
                            WHERE session = DATE '2026-01-06'""").fetchone()
    assert nxt is not None and r != nxt


def test_short_session_is_a_data_gap(con):
    from datetime import date
    minute_bars(con, 2, date(2026, 1, 5), 50.0, n=100)
    minute_bars(con, 2, date(2026, 1, 6), 50.0, n=100)
    I.build(con)
    assert con.execute("SELECT COUNT(*) FROM intraday_features WHERE tok = 2").fetchone()[0] == 0


def test_no_feature_sees_a_bar_after_the_outcome_starts(con):
    """Wildly different prices after 10:30 must not move any feature (the vwap_dev leak)."""
    from datetime import date
    I.build(con)
    base = con.execute("SELECT gap, or_breakout, vwap_dev, rvol_30, range_1h FROM intraday_features "
                       "WHERE session = DATE '2026-01-06'").fetchone()
    con.execute("DELETE FROM minute_bars")
    minute_bars(con, 1, date(2026, 1, 5), 100.0)
    minute_bars(con, 1, date(2026, 1, 6), 102.0, drift=0.0002)
    minute_bars(con, 1, date(2026, 1, 7), 103.0)
    con.execute("UPDATE minute_bars SET open = open * 1.5, high = high * 1.5, low = low * 1.5, "
                "close = close * 1.5, volume = volume * 9 "
                "WHERE CAST(ts AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '2026-01-06' "
                "AND hour(ts AT TIME ZONE 'Asia/Kolkata') * 60 + minute(ts AT TIME ZONE 'Asia/Kolkata') > 630")
    I.build(con)
    after = con.execute("SELECT gap, or_breakout, vwap_dev, rvol_30, range_1h FROM intraday_features "
                        "WHERE session = DATE '2026-01-06'").fetchone()
    assert after == pytest.approx(base)
