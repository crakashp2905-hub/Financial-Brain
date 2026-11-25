from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.execution.exchange_paper import (
    Account,
    ExchangePaperOms,
    Fidelity,
    OmsError,
    OrderStatus,
    PaperOrder,
    option_structure_allowed,
)
from financial_brain.instruments.pit import Quote

T = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)


def quote(t=T, bid="99.95", ask="100.00", bq=1000, aq=1000):
    return Quote(D(bid), D(ask), t, t, bq, aq)


def order(oid="O1", sig="S1", acct=Account.AUTONOMOUS, units=50, side="BUY", limit="100.00",
          lot=1, desk="ETF"):
    return PaperOrder(oid, sig, acct, desk, "IN:X", side, units, D(limit), T + timedelta(hours=1),
                      lot_size=lot)


def oms():
    return ExchangePaperOms(max_quote_age=timedelta(seconds=30))


def test_buy_fills_at_ask_with_fidelity_tag():
    m = oms(); m.submit(order())
    f = m.on_quote("O1", quote(), T + timedelta(seconds=1))
    assert f and f.price == D("100.00") and f.fidelity is Fidelity.OBSERVED_QUOTE_SIM
    assert m.orders["O1"].status is OrderStatus.FILLED


def test_participation_cap_gives_partial_fill():
    m = oms(); m.submit(order(units=500))
    f = m.on_quote("O1", quote(aq=1000), T + timedelta(seconds=1))
    assert f and f.units == 100 and m.orders["O1"].status is OrderStatus.PARTIAL


def test_limit_not_crossed_means_no_fill():
    m = oms(); m.submit(order(limit="99.90"))
    assert m.on_quote("O1", quote(), T + timedelta(seconds=1)) is None


def test_no_quote_no_fill():
    m = oms(); m.submit(order())
    assert m.on_quote("O1", None, T) is None
    assert m.orders["O1"].status is OrderStatus.WORKING


def test_stale_quote_halts_desk_and_blocks_new_orders():
    m = oms(); m.submit(order())
    assert m.on_quote("O1", quote(t=T), T + timedelta(minutes=5)) is None
    assert "ETF" in m.halted
    assert m.submit(order("O2", "S2")).status is OrderStatus.REJECTED


def test_expired_signal_never_fills_on_late_quote():
    m = oms(); m.submit(order())
    late = T + timedelta(hours=2)
    assert m.on_quote("O1", quote(t=late), late) is None
    assert m.orders["O1"].status is OrderStatus.MISSED


def test_one_execution_per_signal_per_account_but_accounts_are_separate():
    m = oms(); m.submit(order())
    with pytest.raises(OmsError):
        m.submit(order("O2"))
    m.submit(order("O3", acct=Account.TRADER_APPROVED))


def test_option_units_must_be_whole_lots():
    with pytest.raises(OmsError):
        order(units=70, lot=75)
    order(units=150, lot=75)


def test_partial_lots_rounded_down_to_lot():
    m = oms(); m.submit(order(units=150, lot=75, desk="OPTIONS"))
    f = m.on_quote("O1", quote(aq=1000), T + timedelta(seconds=1))
    assert f and f.units == 75


def test_naked_writing_blocked():
    assert option_structure_allowed("LONG_CALL")
    assert not option_structure_allowed("SHORT_STRADDLE")
