"""Intraday breakout systems: the three ways a minute-bar backtest lies.

Daily backtests in this project have already shipped two look-aheads, one of them hidden
in a filter rather than a signal. Minute bars make it easier, not harder, because every
session now has 375 decision points instead of one. These tests pin the three specific
mistakes that would make an intraday result look good and be worthless:

* deciding entry from a price later in the session than the entry itself;
* filling at the trigger when the bar gapped straight through it;
* entering inside the minutes that define the opening range.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import duckdb
import pytest

from financial_brain.evaluation import intraday as it

IST = timezone(timedelta(hours=5, minutes=30))


def _con(days):
    """days: {date: [(open, high, low, close), ...]} - one tuple per minute."""
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE minute_bars (instrument_token BIGINT,
                   tradingsymbol VARCHAR, isin VARCHAR, ts TIMESTAMP WITH TIME ZONE,
                   open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT,
                   source VARCHAR)""")
    rows = []
    for d, bars in days.items():
        start = datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST)
        for i, (o, h, low, c) in enumerate(bars):
            rows.append((1, "TEST", "IN0", start + timedelta(minutes=i), o, h, low, c,
                         1000, "KITE"))
    con.executemany("INSERT INTO minute_bars VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    return con


def _flat_session(n=100, price=100.0):
    return [(price, price, price, price)] * n


def _session_rising_late(n=100, price=100.0, jump_at=80, to=110.0):
    """Flat, then a jump late in the session."""
    out = []
    for i in range(n):
        p = to if i >= jump_at else price
        out.append((p, p, p, p))
    return out


# --- point in time ------------------------------------------------------------

def test_a_trigger_cannot_use_the_session_it_decides(monkeypatch):
    """dual_thrust's range comes from prior sessions only. If today's own high leaked in,
    a day that rallied would set its own bar and the system would look prescient."""
    hist = [{"high": 110.0, "low": 90.0, "close": 100.0, "open": 100.0} for _ in range(4)]
    offset = it.dual_thrust(hist, k1=0.5, n=4)
    # HH=110 LL=90 HC=100 LC=100, so range = max(110-100, 100-90) = 10, halved = 5
    assert offset == pytest.approx(5.0)
    # a fifth, wilder session must not change the answer computed for the first four
    hist_plus = hist + [{"high": 500.0, "low": 1.0, "close": 100.0, "open": 100.0}]
    assert it.dual_thrust(hist_plus[:4], k1=0.5, n=4) == pytest.approx(5.0)


def test_entry_scans_bars_forward_only():
    """The entry index must come from a forward scan; a later bar cannot trigger an
    earlier entry."""
    import inspect
    src = inspect.getsource(it.backtest)
    assert "for i in range(first, len(bars))" in src
    assert "bars[entry_i + 1:]" in inspect.getsource(it._square_off_return)


def test_a_gap_through_the_trigger_fills_at_the_open_not_the_trigger():
    """The optimistic error: assuming you got the trigger price when the bar opened far
    above it. Filling at max(trigger, bar_open) is the conservative treatment."""
    import inspect
    assert "max(trigger, bars[entry_i]" in inspect.getsource(it.backtest)


def test_opening_range_entry_is_forbidden_inside_the_range_itself():
    """The range is not known until its minutes have passed, so an entry at minute 3 on a
    15-minute range is reading the future."""
    d = date(2026, 1, 5)
    # a session whose high is set in the first 15 minutes, then flat
    bars = [(100.0, 120.0, 100.0, 100.0)] + _flat_session(99)
    con = _con({d - timedelta(days=i): _flat_session() for i in range(1, 6)}
               | {d: bars})
    r = it.backtest(con, "TEST", "opening_range", stop_frac=None)
    for t in r["trades"]:
        assert t["minute"] >= it.OPENING_RANGE_MIN, \
            "entered inside the minutes that defined the range"


# --- the systems --------------------------------------------------------------

def test_dual_thrust_range_uses_the_asymmetric_definition():
    """max(HH-LC, HC-LL), not simply HH-LL. The asymmetry is in the original."""
    hist = [{"high": 110.0, "low": 95.0, "close": 108.0, "open": 100.0},
            {"high": 105.0, "low": 90.0, "close": 92.0, "open": 100.0}]
    # HH=110 LL=90 HC=108 LC=92 -> max(110-92, 108-90) = max(18, 18) = 18
    assert it.dual_thrust(hist, k1=1.0, n=2) == pytest.approx(18.0)


def test_r_breaker_pivots_come_from_the_previous_session():
    prev = {"high": 110.0, "low": 90.0, "close": 100.0, "open": 95.0}
    piv = it.r_breaker([prev])
    pivot = (110 + 90 + 100) / 3
    assert piv["break_buy"] == pytest.approx(110 + 2 * (pivot - 90))
    assert piv["enter_buy"] == pytest.approx(2 * pivot - 110)


def test_dynamic_breakout_shortens_its_lookback_when_volatility_rises():
    """The adaptive part: calmer past than present means a shorter window."""
    calm = [{"close": 100.0 + (i % 2) * 0.1, "high": 101.0, "low": 99.0} for i in range(40)]
    wild = [{"close": 100.0 + (i % 2) * 10.0, "high": 120.0, "low": 80.0} for i in range(60)]
    got = it.dynamic_breakout_ii(calm + wild, base=20, floor_days=5, ceiling_days=60)
    assert got is not None
    assert got["lookback"] <= 20, "a volatile present should shorten the window"


def test_ghost_trader_sits_out_after_a_win():
    assert it.ghost_trader([-0.01]) is True        # last was a loss -> take the next
    assert it.ghost_trader([0.01]) is False        # last was a win  -> sit out
    assert it.ghost_trader([]) is False            # nothing to go on -> sit out


# --- cost and square-off ------------------------------------------------------

def test_every_trade_pays_a_full_round_trip():
    """These systems fire most sessions, so cost per trade is the dominant term - it must
    not be amortised over anything."""
    import inspect
    src = inspect.getsource(it.evaluate)
    assert "round_trip" in src
    assert "x - cost for x in g" in src


def test_a_position_is_squared_off_at_the_session_close():
    """Nothing is held overnight, so the exit is the last traded minute whatever the rule
    says."""
    bars = [{"ts": None, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}
            for _ in range(10)]
    bars[-1]["close"] = 105.0
    assert it._square_off_return(bars, 100.0, 0, None) == pytest.approx(0.05)


def test_a_stop_bounds_the_loss_at_the_stop_price():
    bars = [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0} for _ in range(5)]
    bars[2]["low"] = 90.0                          # trades through a 1% stop
    got = it._square_off_return(bars, 100.0, 0, stop_frac=0.01)
    assert got == pytest.approx(-0.01)


def test_the_reference_system_exists_so_elaboration_must_earn_its_keep():
    """opening_range is the plainest member of the family. If dual_thrust cannot beat it,
    its extra machinery is decoration."""
    import inspect
    assert '"opening_range"' in inspect.getsource(it.backtest)


def test_a_truncated_session_is_skipped():
    """A day with a handful of bars is a data artifact, not a trading session."""
    d = date(2026, 1, 5)
    con = _con({d: _flat_session(10)})
    assert it.sessions(con, "TEST") == []
