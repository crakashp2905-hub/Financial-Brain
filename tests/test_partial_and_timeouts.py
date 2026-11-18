from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.execution.exchange_paper import Account, ExchangePaperOms, OrderStatus, PaperOrder
from financial_brain.instruments.pit import Quote
from financial_brain.signals.exit_triggers import Bar, Event, ExitError, ProtectedPosition

T = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)


def test_partial_exit_keeps_protection_for_remainder():
    p = ProtectedPosition("P", True, D(10), D(100), D(97), D(106))
    assert p.partial_exit(T, D(4), D(103), "SCALE_OUT") == D(6)
    assert p.partial_exit(T, D(4), D(103), "SCALE_OUT") == D(6)          # duplicate ignored
    p.on_bar(Bar(T + timedelta(minutes=1), D(99), D(99), D(96), D(97)))
    fills = [r for r in p.ledger if r.kind is Event.EXIT_FILL]
    assert [f.quantity for f in fills] == [D(4), D(6)] and p.closed
    assert p.realized() == D(4) * 3 - D(6) * 3


def test_partial_exit_never_oversells():
    p = ProtectedPosition("P", True, D(5), D(100), D(97), D(106))
    assert p.partial_exit(T, D(9), D(101), "MANUAL") == 0 and p.closed
    with pytest.raises(ExitError):
        p.partial_exit(T, D(0), D(1), "x")


def test_trailing_path_with_partial_ladder():
    p = ProtectedPosition("P", True, D(10), D(100), D(97), D(110))
    p.partial_exit(T, D(5), D(105), "TP1")
    p.on_bar(Bar(T + timedelta(minutes=1), D(108), D(111), D(107), D(110)))
    assert p.closed and p.realized() == D(5) * 5 + D(5) * 10


def test_timeout_and_unknown_states():
    m = ExchangePaperOms(max_quote_age=timedelta(seconds=5))
    m.submit(PaperOrder("A", "S1", Account.AUTONOMOUS, "ETF", "X", "BUY", 100, D(100), T + timedelta(hours=1)))
    assert m.timeout("A", T + timedelta(seconds=10), T + timedelta(seconds=5)) is OrderStatus.TIMED_OUT
    m.submit(PaperOrder("B", "S2", Account.AUTONOMOUS, "ETF", "X", "BUY", 500, D(100), T + timedelta(hours=1)))
    m.on_quote("B", Quote(D("99.9"), D("100"), T, T, 1000, 1000), T)
    assert m.timeout("B", T + timedelta(seconds=10), T + timedelta(seconds=5)) is OrderStatus.UNKNOWN
