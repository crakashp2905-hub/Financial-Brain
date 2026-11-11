from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.schemas import (
    BarEvent,
    BookSnapshot,
    CorporateAction,
    Fill,
    Intent,
    Order,
    Position,
    Quote,
    Signal,
    Trade,
)

T = datetime(2026, 11, 9, 4, 0, tzinfo=timezone.utc)
IST_NAIVE = datetime(2026, 11, 9, 9, 15)
IST = timezone(timedelta(hours=5, minutes=30))


def test_trade_and_quote_require_utc_and_positive_values() -> None:
    assert Trade("X", T, Decimal("10.5"), Decimal("1")).price == Decimal("10.5")
    with pytest.raises(ValueError):
        Trade("X", IST_NAIVE, Decimal("1"), Decimal("1"))
    with pytest.raises(ValueError):
        Trade("X", datetime(2026, 11, 9, 9, 15, tzinfo=IST), Decimal("1"), Decimal("1"))
    with pytest.raises(ValueError):
        Quote("X", T, Decimal("0"), Decimal("1"))


def test_crossed_quote_and_book_are_refused() -> None:
    with pytest.raises(ValueError, match="crossed quote"):
        Quote("X", T, Decimal("11"), Decimal("10"))
    with pytest.raises(ValueError, match="crossed book"):
        BookSnapshot("X", T, ((Decimal("10"), Decimal("1")),), ((Decimal("10"), Decimal("1")),))


def test_bar_must_bracket_open_and_close() -> None:
    BarEvent("X", T, Decimal("10"), Decimal("12"), Decimal("9"), Decimal("11"), Decimal("100"))
    with pytest.raises(ValueError, match="bracket"):
        BarEvent("X", T, Decimal("10"), Decimal("10.5"), Decimal("9"), Decimal("11"), Decimal("100"))


def test_corporate_action_kinds_are_closed() -> None:
    CorporateAction("X", T, "split", Decimal("2"))
    with pytest.raises(ValueError, match="unknown corporate action"):
        CorporateAction("X", T, "buyback", Decimal("2"))


def test_order_intent_fill_position_validation() -> None:
    with pytest.raises(ValueError, match="client_id"):
        Order("", "X", T, "BUY", Decimal("1"))
    with pytest.raises(ValueError):
        Intent("s", "X", T, "HOLD", Decimal("1"))
    with pytest.raises(ValueError):
        Fill("c1", T, Decimal("10"), Decimal("-1"))
    assert Position("X", T, Decimal("-5")).quantity == Decimal("-5")


def test_signal_direction_is_closed_set() -> None:
    assert Signal("s", "X", T, -1).direction == -1
    with pytest.raises(ValueError):
        Signal("s", "X", T, 2)
