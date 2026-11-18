from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from financial_brain.instruments.market_quality import (
    NavRevisions,
    QualityError,
    Tick,
    bars_from_ticks,
    dedupe_alerts,
    eod_rows,
    gap_open_fill,
    liquidity,
    merge_corrections,
    sequence_gaps,
    tick_ok,
)

IST = timezone(timedelta(hours=5, minutes=30))
T = datetime(2026, 11, 17, 10, 0, tzinfo=IST)


def test_eod_corrections_are_kept_not_overwritten():
    a = eod_rows([{"symbol": "E", "date": "2026-11-17", "close": 100}], "NSE")
    b = eod_rows([{"symbol": "E", "date": "2026-11-17", "close": 101}], "NSE")
    m = merge_corrections(a, b + a)
    assert len(m) == 2 and m[1]["correction_of"] == a[0]["content_hash"]


def test_bars_from_ticks_session_gated():
    ticks = [(T.replace(hour=9, minute=10), 1.0, 1), (T, 100.0, 5), (T + timedelta(seconds=30), 101.0, 5),
             (T + timedelta(minutes=1), 99.0, 1)]
    b = bars_from_ticks(ticks, time(9, 15), time(15, 30), IST)
    assert len(b) == 2 and b[0]["high"] == 101 and b[0]["volume"] == 10


def test_liquidity_metrics():
    q = [(T, 99.9, 100.1, 100, 200), (T + timedelta(minutes=5), 99.8, 100.2, 50, 50)]
    m = liquidity(q, [1e6, 3e6], T + timedelta(minutes=6), timedelta(minutes=2))
    assert m["gaps"] == 1 and m["median_traded_value"] == 2e6 and m["stale_quote_rate"] == 0.5
    with pytest.raises(QualityError):
        liquidity([], [], T, timedelta(1))


def test_nav_revisions_not_backdated():
    r = NavRevisions()
    r.add("X", date(2026, 11, 16), 10.0, T - timedelta(hours=12), T - timedelta(hours=11))
    r.add("X", date(2026, 11, 16), 10.2, T - timedelta(hours=1), T)
    assert r.as_known("X", date(2026, 11, 16), T - timedelta(hours=2)) == 10.0
    assert r.as_known("X", date(2026, 11, 16), T) == 10.2
    assert r.as_known("X", date(2026, 11, 16), T - timedelta(days=1)) is None


def test_dedupe_alerts_keeps_new_version_and_material_moves():
    a = [dict(signal="S", kind="BUY", at=1, rule_version="v1", price=100.0),
         dict(signal="S", kind="BUY", at=2, rule_version="v1", price=100.1),
         dict(signal="S", kind="BUY", at=3, rule_version="v2", price=100.1),
         dict(signal="S", kind="BUY", at=4, rule_version="v2", price=103.0)]
    assert [x["at"] for x in dedupe_alerts(a, 0.01)] == [1, 3, 4]


def test_tick_checks():
    good = Tick(99.95, 100.0, 100, 100, T, T)
    assert tick_ok(good, T, max_age=timedelta(seconds=5), max_spread_bps=20, min_depth=10) == []
    ltp_only = Tick(0, 0, 0, 0, None, T)
    assert "no two-sided book" in tick_ok(ltp_only, T, max_age=timedelta(seconds=5), max_spread_bps=20, min_depth=10)
    circ = Tick(99.95, 100.0, 100, 100, T, T, upper_circuit=100.0, halted=True)
    w = tick_ok(circ, T, max_age=timedelta(seconds=5), max_spread_bps=20, min_depth=10)
    assert "halted" in w and "at upper circuit" in w


def test_sequence_gaps():
    assert sequence_gaps([1, 2, 5, 6, 9]) == [(2, 5), (6, 9)]


def test_gap_and_circuit_fills():
    assert gap_open_fill(True, 97, 90, None, None) == 90
    assert gap_open_fill(True, 97, 99, None, None) == 97
    assert gap_open_fill(True, 97, 85, 85, None) is None
    assert gap_open_fill(False, 103, 110, None, 110) is None
