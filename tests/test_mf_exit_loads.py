from datetime import date
from decimal import Decimal

import pytest

from financial_brain.paper.mf_exit_loads import (
    ExitLoadError, PurchaseLot, calculate_exit_loads, calculate_transaction_charges,
)
from financial_brain.providers.mf_scheme_terms import ExitLoadBand, MFChargeRule


def test_partial_redemption_charges_each_fifo_lot_with_its_own_load():
    schedule = (ExitLoadBand(0, Decimal("1.00")), ExitLoadBand(91, Decimal("0.00")))
    lots = (
        PurchaseLot("later", date(2026, 2, 1), Decimal("5"), schedule),
        PurchaseLot("early", date(2026, 1, 1), Decimal("10"), schedule),
    )

    result = calculate_exit_loads(
        lots, redemption_date=date(2026, 2, 15),
        redemption_units=Decimal("12"), nav_per_unit=Decimal("100"),
    )

    assert result.gross_redemption_inr == Decimal("1200")
    assert result.exit_load_inr == Decimal("12.00")
    assert result.net_redemption_inr == Decimal("1188.00")
    assert [(x.lot_id, x.units_redeemed, x.units_remaining) for x in result.allocations] == [
        ("early", Decimal("10"), Decimal("0")),
        ("later", Decimal("2"), Decimal("3")),
    ]


def test_full_redemption_uses_zero_rate_after_the_threshold():
    lot = PurchaseLot(
        "lot", date(2026, 1, 1), Decimal("5"),
        (ExitLoadBand(0, Decimal("1")), ExitLoadBand(90, Decimal("0"))),
    )
    result = calculate_exit_loads(
        (lot,), redemption_date=date(2026, 4, 1),
        redemption_units=Decimal("5"), nav_per_unit=Decimal("25.5"),
    )
    assert result.exit_load_inr == Decimal("0.00")
    assert result.allocations[0].units_remaining == 0


def test_unknown_exit_load_or_overredemption_fails_closed():
    lot = PurchaseLot("lot", date(2026, 1, 1), Decimal("3"), None)
    with pytest.raises(ExitLoadError, match="unknown"):
        calculate_exit_loads(
            (lot,), redemption_date=date(2026, 1, 3),
            redemption_units=Decimal("1"), nav_per_unit=Decimal("10"),
        )
    clear_schedule = PurchaseLot("clear", date(2026, 1, 1), Decimal("3"), ())
    with pytest.raises(ExitLoadError, match="exceed"):
        calculate_exit_loads(
            (clear_schedule,), redemption_date=date(2026, 1, 3),
            redemption_units=Decimal("4"), nav_per_unit=Decimal("10"),
        )


def test_explicit_empty_schedule_means_zero_load():
    lot = PurchaseLot("lot", date(2026, 1, 1), Decimal("3"), ())
    result = calculate_exit_loads(
        (lot,), redemption_date=date(2026, 1, 3),
        redemption_units=Decimal("2"), nav_per_unit=Decimal("10"),
    )
    assert result.exit_load_inr == Decimal("0.00")


def test_transaction_charges_are_per_rule_paise_rounded_and_unknown_fails_closed():
    rules = (
        MFChargeRule("STAMP_DUTY", "SUBSCRIBE", Decimal("0.005")),
        MFChargeRule("TRANSACTION_FEE", "SUBSCRIBE", Decimal("0.01")),
        MFChargeRule("REDEMPTION_FEE", "REDEEM", Decimal("0.25")),
    )
    amount, breakdown = calculate_transaction_charges(
        rules, transaction="SUBSCRIBE", notional_inr=Decimal("100000"),
    )
    assert amount == Decimal("15.00")
    assert breakdown == (("STAMP_DUTY", Decimal("5.00")),
                         ("TRANSACTION_FEE", Decimal("10.00")))
    assert calculate_transaction_charges(
        (), transaction="REDEEM", notional_inr=Decimal("1000"),
    ) == (Decimal(0), ())
    with pytest.raises(ExitLoadError, match="unknown"):
        calculate_transaction_charges(
            None, transaction="SUBSCRIBE", notional_inr=Decimal("1000"),
        )
