from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.accounting_extra import (
    EodSnapshot,
    borrow_interest,
    fx_pnl_split,
    futures_liquidation_price,
    margin_call,
    margin_requirement,
    stress_breach,
)


def test_interest_only_on_negative_cash() -> None:
    assert borrow_interest(Decimal("1000"), Decimal("0.12"), 30) == Decimal("0")
    assert borrow_interest(Decimal("-365000"), Decimal("0.12"), 30) == Decimal("3600")


def test_fx_split_sums_to_total() -> None:
    parts = fx_pnl_split(
        quantity=Decimal("100"),
        price_entry=Decimal("10"),
        price_exit=Decimal("12"),
        fx_entry=Decimal("80"),
        fx_exit=Decimal("83"),
    )
    assert parts["price"] + parts["fx"] == parts["total"]
    assert parts["total"] == Decimal("100") * Decimal("12") * Decimal("83") - Decimal("100") * Decimal("10") * Decimal("80")


def test_margin_requirement_and_call_event() -> None:
    assert margin_requirement(Decimal("-200000"), Decimal("0.1")) == Decimal("20000")
    assert margin_call("2026-11-09", Decimal("15000"), Decimal("20000")) is not None
    assert margin_call("2026-11-09", Decimal("25000"), Decimal("20000")) is None
    with pytest.raises(ValueError):
        margin_requirement(Decimal("1"), Decimal("0"))


def test_eod_snapshot_is_immutable_per_day() -> None:
    snap = EodSnapshot()
    digest = snap.freeze("2026-11-09", {"NIFTY": "2"}, "5000")
    assert len(digest) == 64
    with pytest.raises(ValueError, match="already frozen"):
        snap.freeze("2026-11-09", {"NIFTY": "3"}, "5000")
    assert snap.freeze("2026-11-10", {"NIFTY": "3"}, "5000") != digest


def test_futures_liquidation_and_stress() -> None:
    assert futures_liquidation_price(Decimal("100"), Decimal("10"), "LONG") == Decimal("90")
    assert futures_liquidation_price(Decimal("100"), Decimal("10"), "SHORT") == Decimal("110")
    assert stress_breach(Decimal("100"), Decimal("10"), "LONG", Decimal("0.10")) is True
    assert stress_breach(Decimal("100"), Decimal("10"), "LONG", Decimal("0.05")) is False
    assert stress_breach(Decimal("100"), Decimal("10"), "SHORT", Decimal("0.10")) is True
