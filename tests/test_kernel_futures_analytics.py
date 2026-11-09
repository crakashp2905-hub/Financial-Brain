from __future__ import annotations

from decimal import Decimal

from financial_brain.kernel.futures_analytics import (
    basis_vs_fair,
    calendar_spread,
    expiry_settlement,
    fair_futures,
    hedge_contracts,
    within_daily_limit,
)


def test_calendar_spread_sign_shows_contango() -> None:
    assert calendar_spread(Decimal("100"), Decimal("102")) == Decimal("2")
    assert calendar_spread(Decimal("100"), Decimal("98")) == Decimal("-2")


def test_basis_against_fair_value() -> None:
    fair = fair_futures(Decimal("1000"), Decimal("0.06"), Decimal("0.02"), 365)
    assert fair == Decimal("1040")
    assert basis_vs_fair(Decimal("1045"), Decimal("1000"), Decimal("0.06"), Decimal("0.02"), 365) == Decimal("5")


def test_hedge_contracts_round_toward_zero() -> None:
    assert hedge_contracts(Decimal("1050000"), Decimal("200000")) == 5
    assert hedge_contracts(Decimal("-1050000"), Decimal("200000")) == -5


def test_cash_settles_and_physical_refuses_shortfall() -> None:
    cash = expiry_settlement("CASH", 2, Decimal("110"), Decimal("100"), 0)
    assert cash["cash"] == Decimal("20")
    short = expiry_settlement("PHYSICAL", 2, Decimal("110"), Decimal("100"), 1)
    assert short["status"] == "DELIVERY_SHORTFALL"
    assert short["delivered"] == 0


def test_daily_limit_check() -> None:
    assert within_daily_limit(Decimal("104"), Decimal("100"), Decimal("0.05")) is True
    assert within_daily_limit(Decimal("106"), Decimal("100"), Decimal("0.05")) is False
