from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.signals.exit_triggers import Bar, Event, ExitError, ProtectedPosition

T = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)


def bar(i, o, h, lo, c):
    v = [None if x is None else D(str(x)) for x in (o, h, lo, c)]
    return Bar(T + timedelta(minutes=15 * i), *v)


def pos(long=True):
    if long:
        return ProtectedPosition("P1", True, D(10), D(100), D(97), D(106))
    return ProtectedPosition("P1", False, D(10), D(100), D(103), D(94))


def fills(p):
    return [r for r in p.ledger if r.kind is Event.EXIT_FILL]


def test_stop_only():
    p = pos(); p.on_bar(bar(0, 99, 100, 96, 98))
    assert fills(p)[0].price == D(97) and p.closed and p.realized() == D(-30)


def test_target_only():
    p = pos(); p.on_bar(bar(0, 101, 107, 100, 105))
    assert fills(p)[0].reason == "TAKE_PROFIT" and p.realized() == D(60)


def test_both_touched_is_adverse_first():
    p = pos(); p.on_bar(bar(0, 100, 107, 96, 105))
    assert fills(p)[0].reason == "HARD_STOP"


def test_gap_through_stop_fills_at_open_keeping_extra_loss():
    p = pos(); p.on_bar(bar(0, 90, 92, 89, 91))
    assert fills(p)[0].price == D(90) and p.realized() == D(-100)


def test_gap_through_target_fills_at_open():
    p = pos(); p.on_bar(bar(0, 110, 111, 109, 110))
    assert fills(p)[0].price == D(110)


def test_short_stop_and_target():
    p = pos(False); p.on_bar(bar(0, 100, 104, 99, 101))
    assert fills(p)[0].reason == "HARD_STOP" and p.realized() == D(-30)
    q = pos(False); q.on_bar(bar(0, 99, 100, 93, 95))
    assert fills(q)[0].reason == "TAKE_PROFIT" and q.realized() == D(60)


def test_no_double_exit_and_idempotent_replay():
    p = pos(); b = bar(0, 99, 100, 96, 98)
    p.on_bar(b); p.on_bar(b); p.on_bar(bar(1, 90, 91, 80, 85))
    assert len(fills(p)) == 1 and p.open_quantity == 0


def test_halt_leaves_unresolved_intent_then_fills_at_next_price():
    p = pos()
    p.trigger_without_price(T, "HARD_STOP")
    p.on_bar(bar(1, None, None, None, None))
    assert not p.closed and not fills(p)
    assert any(r.kind is Event.EXIT_UNRESOLVED for r in p.ledger)
    p.on_bar(bar(2, 95, 96, 94, 95))
    assert fills(p)[0].price == D(95) and p.closed


def test_untouched_bar_does_nothing():
    p = pos(); p.on_bar(bar(0, 100, 101, 99, 100))
    assert not p.ledger and not p.closed


@pytest.mark.parametrize("stop,target", [(101, 106), (97, 99)])
def test_wrong_side_protection_refused(stop, target):
    with pytest.raises(ExitError):
        ProtectedPosition("P", True, D(1), D(100), D(stop), D(target))


def test_zero_quantity_refused():
    with pytest.raises(ExitError):
        ProtectedPosition("P", True, D(0), D(100), D(97), D(106))
