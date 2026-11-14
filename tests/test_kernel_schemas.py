from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.schemas import (
    AssetClass,
    BarEvent,
    BookSnapshot,
    CorporateAction,
    Fill,
    FillEvent,
    InstrumentRef,
    Intent,
    IntentEvent,
    Order,
    OrderEvent,
    OrderStatus,
    EventSide,
    MarkEvent,
    Position,
    Quote,
    Signal,
    Trade,
    canonical_event_payload,
    trading_event_sha256,
)

T = datetime(2026, 11, 9, 4, 0, tzinfo=timezone.utc)
IST_NAIVE = datetime(2026, 11, 9, 9, 15)
IST = timezone(timedelta(hours=5, minutes=30))
SOURCE = "a" * 64


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


def test_common_order_event_contract_supports_cash_futures_and_options():
    cash = InstrumentRef("NSE", "INFY", AssetClass.EQUITY)
    future = InstrumentRef("NFO", "NIFTY", AssetClass.FUTURE,
                           expiry=date(2026, 11, 26), multiplier=Decimal("25"), lot_size=25)
    option = InstrumentRef("NFO", "NIFTY", AssetClass.OPTION,
                           expiry=date(2026, 11, 26), multiplier=Decimal("25"), lot_size=25,
                           strike=Decimal("25000"), option_right="CE")
    intent = IntentEvent("evt-1", "ord-1", "paper-a", "momentum-v1", option,
                         EventSide.BUY, 50, T, SOURCE)
    status = OrderEvent("evt-2", "ord-1", OrderStatus.ACCEPTED, T, SOURCE)
    fill = FillEvent("evt-3", "ord-1", option, EventSide.BUY, 25, Decimal("100"), T,
                     "SIMULATED_QUOTE", SOURCE)
    mark = MarkEvent("evt-4", future, Decimal("200"), T, "SIMULATED_BAR", SOURCE)

    assert cash.key == "NSE:EQUITY:INFY"
    assert future.key == "NFO:FUTURE:NIFTY:2026-11-26"
    assert option.key.endswith(":25000:CE")
    assert intent.instrument == option and status.status is OrderStatus.ACCEPTED
    assert fill.quantity == 25 and mark.instrument == future
    payload = canonical_event_payload(intent)
    assert b'"asset_class":"OPTION"' in payload
    assert trading_event_sha256(intent) == trading_event_sha256(intent)


def test_derivative_contracts_and_event_provenance_fail_closed():
    with pytest.raises(ValueError, match="AssetClass"):
        InstrumentRef("NSE", "INFY", "EQUITY")
    with pytest.raises(ValueError, match="futures require expiry"):
        InstrumentRef("NFO", "NIFTY", AssetClass.FUTURE)
    with pytest.raises(ValueError, match="options require"):
        InstrumentRef("NFO", "NIFTY", AssetClass.OPTION, expiry=date(2026, 11, 26))
    option = InstrumentRef("NFO", "NIFTY", AssetClass.OPTION, lot_size=25,
                           expiry=date(2026, 11, 26), strike=Decimal("25000"), option_right="PE")
    with pytest.raises(ValueError, match="lot-size multiple"):
        IntentEvent("e", "o", "a", "s", option, EventSide.BUY, 1, T, SOURCE)
    with pytest.raises(ValueError, match="SHA-256"):
        MarkEvent("m", option, Decimal("1"), T, "SIMULATED_QUOTE", "bad")
    with pytest.raises(ValueError, match="fidelity"):
        FillEvent("f", "o", option, EventSide.BUY, 25, Decimal("1"), T, "REPLAY", SOURCE)
