from __future__ import annotations

from datetime import datetime, timedelta, timezone

from financial_brain.paper.control_room import (
    Alert,
    AlertBook,
    Approval,
    Kind,
    SessionState,
    approval_valid,
    post_market_report,
    session_gate,
)

T = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)


def alert(key, kind, desk="SWING", text="x", dt=0):
    return Alert(key, kind, desk, T + timedelta(minutes=dt), text, "v1", "LOCAL_PAPER")


def test_alerts_idempotent_and_risk_first():
    b = AlertBook()
    assert b.emit(alert("a", Kind.BUY, dt=0))
    assert b.emit(alert("b", Kind.STOP_HIT, dt=5))
    assert not b.emit(alert("a", Kind.BUY, dt=9))
    assert [a.kind for a in b.today()] == [Kind.STOP_HIT, Kind.BUY]
    assert b.today()[0].level == 1 and b.today()[1].level == 2


def test_today_filters_by_desk_and_search():
    b = AlertBook()
    b.emit(alert("a", Kind.BUY, desk="ETF", text="NIFTYBEES breakout"))
    b.emit(alert("b", Kind.WATCH, desk="SWING", text="other"))
    assert [a.key for a in b.today(desk="ETF")] == ["a"]
    assert [a.key for a in b.today(search="niftybees")] == ["a"]


def test_expired_approval_not_reused():
    a = Approval("S", T, T + timedelta(minutes=10))
    assert approval_valid(a, T + timedelta(minutes=5))
    assert not approval_valid(a, T + timedelta(minutes=10))


def test_session_gate():
    ok = SessionState(0, 0, 0, True)
    assert session_gate(ok)[0] == "SESSION_COMPLETE"
    bad = SessionState(2, 1, 0, True)
    status, why = session_gate(bad)
    assert status == "SESSION_INCOMPLETE" and len(why) == 2
    flagged = SessionState(2, 0, 0, True, frozenset({"open_orders"}))
    assert session_gate(flagged)[0] == "SESSION_COMPLETE"
    assert session_gate(SessionState(0, 0, 0, False))[0] == "SESSION_INCOMPLETE"


def test_post_market_report_is_deterministic():
    kw = dict(issued=5, eligible=3, missed=1, filled=2, exits_by_reason={"TP": 1, "SL": 1},
              net_pnl=-120.5, fees=40.0, skipped=2, breaches=[], obligations=["MF allotment"])
    r1 = post_market_report("SWING", **kw)
    assert r1 == post_market_report("SWING", **kw)
    assert "exits: SL 1, TP 1" in r1 and "net P&L -120.50" in r1
