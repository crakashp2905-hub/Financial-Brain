from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.oms import InsufficientCash, Oms, OmsError, State


def _oms() -> Oms:
    return Oms({"paper-inr": Decimal("100000")})


def _buy(oms: Oms, cid: str = "c1", qty: int = 100, reserve: str = "50000"):
    return oms.submit_intent(
        client_id=cid, account="paper-inr", side="BUY", quantity=qty,
        limit=Decimal("500"), reserve=Decimal(reserve),
    )


def test_same_intent_replayed_creates_one_order() -> None:
    oms = _oms()
    a = _buy(oms)
    b = _buy(oms)
    assert a is b
    assert oms.available("paper-inr") == Decimal("50000")


def test_client_id_reuse_with_different_content_is_refused() -> None:
    oms = _oms()
    _buy(oms)
    with pytest.raises(OmsError, match="reused"):
        _buy(oms, qty=200)


def test_two_orders_cannot_spend_the_same_cash() -> None:
    oms = _oms()
    _buy(oms, "c1", reserve="60000")
    with pytest.raises(InsufficientCash):
        _buy(oms, "c2", reserve="60000")


def test_illegal_transition_refused() -> None:
    oms = _oms()
    _buy(oms)
    with pytest.raises(OmsError, match="illegal transition"):
        oms.move("c1", State.FILLED)  # cannot fill before acceptance


def test_full_lifecycle_releases_reservation() -> None:
    oms = _oms()
    _buy(oms)
    oms.move("c1", State.SUBMITTED)
    oms.move("c1", State.ACCEPTED)
    oms.record_fill("c1", 40, Decimal("499"))
    assert oms.order("c1").state is State.PARTIAL
    oms.record_fill("c1", 60, Decimal("498"))
    assert oms.order("c1").state is State.FILLED
    # buying power falls by the spent notional (40*499 + 60*498 = 49840); reservation fully released
    assert oms.cash("paper-inr") == Decimal("50160")
    assert oms.available("paper-inr") == Decimal("50160")


def test_unknown_outcome_blocks_resend_until_reconciled() -> None:
    oms = _oms()
    _buy(oms)
    oms.move("c1", State.SUBMITTED)
    oms.move("c1", State.UNKNOWN)
    assert oms.resend_allowed("c1") is False
    oms.move("c1", State.ACCEPTED)  # reconciliation found it live


def test_fill_after_cancel_request_is_still_accounted() -> None:
    oms = _oms()
    _buy(oms)
    oms.move("c1", State.SUBMITTED)
    oms.move("c1", State.ACCEPTED)
    oms.move("c1", State.CANCEL_PENDING)
    oms.record_fill("c1", 30, Decimal("499"))
    assert oms.order("c1").filled == 30
    assert oms.order("c1").fills == [(30, Decimal("499"))]


def test_kill_all_cancels_working_orders_and_keeps_fills() -> None:
    oms = _oms()
    _buy(oms, "c1")
    oms.move("c1", State.SUBMITTED)
    oms.move("c1", State.ACCEPTED)
    oms.record_fill("c1", 10, Decimal("500"))
    assert oms.kill_all() == ["c1"]
    assert oms.order("c1").filled == 10
    oms.move("c1", State.CANCELED)
    # 10 shares were bought at 500 (5000 spent); the unfilled 90 shares' reservation is released
    assert oms.available("paper-inr") == Decimal("95000")


def test_fill_cannot_exceed_remaining() -> None:
    oms = _oms()
    _buy(oms, qty=10, reserve="1000")
    oms.move("c1", State.SUBMITTED)
    oms.move("c1", State.ACCEPTED)
    with pytest.raises(OmsError, match="out of range"):
        oms.record_fill("c1", 11, Decimal("1"))


def test_cash_adjustment_is_idempotent_and_preserves_reservations() -> None:
    oms = _oms()
    _buy(oms, reserve="90000")
    assert oms.apply_cash_adjustment("paper-inr", Decimal("-100"), adjustment_id="fee-1") is True
    assert oms.apply_cash_adjustment("paper-inr", Decimal("-100"), adjustment_id="fee-1") is False
    assert oms.available("paper-inr") == Decimal("9900")
    with pytest.raises(OmsError, match="different content"):
        oms.apply_cash_adjustment("paper-inr", Decimal("-101"), adjustment_id="fee-1")
    with pytest.raises(InsufficientCash):
        oms.apply_cash_adjustment("paper-inr", Decimal("-11000"), adjustment_id="fee-2")
