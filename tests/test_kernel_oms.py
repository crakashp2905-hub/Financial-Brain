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
    assert oms.available("paper-inr") == Decimal("100000")


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
    assert oms.available("paper-inr") == Decimal("100000")


def test_fill_cannot_exceed_remaining() -> None:
    oms = _oms()
    _buy(oms, qty=10, reserve="1000")
    oms.move("c1", State.SUBMITTED)
    oms.move("c1", State.ACCEPTED)
    with pytest.raises(OmsError, match="out of range"):
        oms.record_fill("c1", 11, Decimal("1"))
