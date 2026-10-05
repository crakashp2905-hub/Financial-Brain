import inspect
from datetime import datetime

import pytest

from financial_brain.paper import intraday as I


def at(h, m, s=0):
    return datetime(2026, 10, 6, h, m, s)


def engine(prev=100.0, open_px=100.0, hi=101.0, lo=99.0, strategies=("ORB",)):
    e = I.DayEngine("2026-10-06", strategies=strategies)
    e.add_symbol("X", prev)
    e.set_opening("X", open_px=open_px, or_hi=hi, or_lo=lo)
    return e


def test_nothing_happens_before_the_range_is_set_or_inside_it():
    e = I.DayEngine("2026-10-06")
    e.add_symbol("X", 100.0)
    assert e.on_price("X", at(9, 35), 105.0) == []          # no opening range yet
    e = engine()
    assert e.on_price("X", at(9, 35), 100.5) == []          # inside the range


def test_orb_long_opens_once_and_hits_its_target():
    e = engine()
    t = e.on_price("X", at(9, 40), 101.5)[0]
    assert (t.side, t.stop, t.target) == ("long", 99.0, pytest.approx(101.5 + 2 * 2.5))
    assert e.on_price("X", at(9, 41), 102.0) == []         # one trade per strategy per day
    out = e.on_price("X", at(10, 30), 106.6)
    assert out[0].exit_reason == "target" and out[0].r_multiple == pytest.approx((106.6 - 101.5) / 2.5)
    assert out[0].net_pnl < out[0].gross_pnl                 # costs charged


def test_size_is_the_risk_budget_over_the_stop_and_capped_by_weight():
    e = engine()
    t = e.on_price("X", at(9, 40), 101.5)[0]
    assert t.qty == int(min(0.0025 * 1_000_000 / 2.5, 0.10 * 1_000_000 / 101.5))
    assert t.risk_inr <= 0.0025 * 1_000_000 + 1e-6


def test_no_new_entries_after_11_and_everything_flat_at_15_15():
    e = engine()
    assert e.on_price("X", at(11, 5), 105.0) == []
    e2 = engine()
    e2.on_price("X", at(9, 40), 101.5)
    out = e2.on_price("X", at(15, 15), 101.6)
    assert out[0].exit_reason == "time"


def test_end_of_day_closes_what_is_left_and_stops_the_engine():
    e = engine()
    e.on_price("X", at(9, 40), 101.5)
    e.end_of_day(at(15, 20), {"X": 100.0})
    assert not e.trades[0].is_open and e.closed_for_day
    assert e.on_price("X", at(15, 21), 110.0) == []


def test_short_side_and_stop():
    e = engine()
    t = e.on_price("X", at(9, 45), 98.5)[0]
    assert t.side == "short" and t.stop == 101.0
    out = e.on_price("X", at(9, 50), 101.2)
    assert out[0].exit_reason == "stop" and out[0].gross_pnl < 0


def test_gap_fade_shorts_a_gap_up_that_slips_below_its_open_and_targets_the_previous_close():
    e = engine(prev=100.0, open_px=103.0, hi=104.0, lo=102.5, strategies=("GAP",))
    t = e.on_price("X", at(9, 35), 102.8)[0]
    assert t.side == "short" and t.target == 100.0 and t.stop == 104.0
    out = e.on_price("X", at(12, 0), 99.9)
    assert out[0].exit_reason == "target" and out[0].gross_pnl > 0


def test_no_gap_means_no_gap_trade():
    e = engine(prev=100.0, open_px=100.4, hi=101, lo=99.5, strategies=("GAP",))
    assert e.on_price("X", at(9, 35), 99.6) == []
    assert "GAP" in e.symbols["X"].done


def test_exposure_limit_skips_and_records_why():
    e = I.DayEngine("2026-10-06", account=10_000, strategies=("ORB",))
    for k in range(80):
        e.add_symbol(f"S{k}", 100.0)
        e.set_opening(f"S{k}", open_px=100.0, or_hi=100.2, or_lo=99.8)
    for k in range(80):
        e.on_price(f"S{k}", at(9, 40), 100.5)
    assert e.open_exposure() <= 5.0 * 10_000 + 200
    assert any(s["why"] == "intraday exposure limit reached" for s in e.skipped)


def test_summary_counts_and_the_module_cannot_place_an_order():
    e = engine()
    e.on_price("X", at(9, 40), 101.5)
    e.end_of_day(at(15, 20), {"X": 102.0})
    s = e.summary()["ORB"]
    assert s["trades"] == 1 and s["by_exit"]["time"] == 1
    src = inspect.getsource(I).lower()
    for w in ("urllib", "requests", "kiteconnect", "place_order", "/orders", "socket"):
        assert w not in src
