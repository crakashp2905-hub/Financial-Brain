from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.paper.desk_runs import IntradayPosition, RunError, publish_daily

IST = timezone(timedelta(hours=5, minutes=30))
T = datetime(2026, 11, 17, 10, 0, tzinfo=IST)


def short():
    return IntradayPosition("SELL_SHORT", D(10), D(100), D(103), D(94), time(15, 15), IST)


def test_short_stop_and_cover_side():
    p = short()
    assert p.close_action == "BUY_TO_COVER"
    p.on_bar(T, D(100), D(104), D(99), D(103))
    assert p.events[0][0] == "HARD_STOP" and p.pnl() == D(-30)


def test_short_target_and_gap():
    p = short(); p.on_bar(T, D(92), D(93), D(91), D(92))
    assert p.events[0] == ("TAKE_PROFIT", T, D(92)) and p.pnl() == D(80)


def test_mandatory_flatten_and_missed_exit():
    p = short(); p.on_bar(T.replace(hour=15, minute=15), D(99), D(99), D(98), D(98))
    assert p.events[0][0] == "SESSION_FLATTEN" and not p.open
    q = short(); q.on_bar(T.replace(hour=15, minute=16), None, None, None, None)
    assert q.events[0][0] == "MISSED_EXIT" and q.open


def test_invalid_plan():
    with pytest.raises(RunError):
        IntradayPosition("SELL_SHORT", D(1), D(100), D(97), D(106), time(15, 15), IST)


def test_publish_daily():
    p = publish_daily([{"symbol": f"S{i}", "net_ev": i - 2} for i in range(9)], [("E1", 0.1)],
                      for_session="2026-11-18")
    assert p["swing"] == ["S8", "S7", "S6", "S5", "S4"] and p["etf_rank"] == ["E1"] and p["locked"]
    assert publish_daily([], [], for_session="x")["swing"] == []
