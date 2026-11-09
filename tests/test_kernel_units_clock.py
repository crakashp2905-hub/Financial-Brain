from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.clock import ClockError, Instants, assert_not_future, utc
from financial_brain.kernel.units import Money, Price, Quantity, UnitError

IST = timezone(timedelta(hours=5, minutes=30))


def test_money_arithmetic_is_exact_decimal() -> None:
    total = Money.of("0.10", "INR") + Money.of("0.20", "INR")
    assert total.amount == Decimal("0.30")


def test_money_refuses_currency_mixing() -> None:
    with pytest.raises(UnitError, match="currency mismatch"):
        _ = Money.of(1, "USD") + Money.of(1, "INR")


def test_money_refuses_price_and_quantity_mixing() -> None:
    with pytest.raises(UnitError):
        _ = Money.of(1, "USD") + Price(Decimal("1"), "USD")  # type: ignore[operator]
    with pytest.raises(UnitError):
        _ = Quantity(Decimal("1")) + Money.of(1, "USD")  # type: ignore[operator]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "abc", None])
def test_non_finite_or_non_numeric_amounts_rejected(bad: object) -> None:
    with pytest.raises(UnitError):
        Money.of(bad, "USD")  # type: ignore[arg-type]


def test_currency_must_be_three_upper_case_letters() -> None:
    for code in ("usd", "US", "USDX", "1RS"):
        with pytest.raises(UnitError):
            Money.of(1, code)


def test_float_conversion_uses_shortest_decimal() -> None:
    assert Money.of(0.1, "USD").amount == Decimal("0.1")
    assert math.isfinite(float(Money.of(2.5, "USD").amount))


def test_price_notional_applies_multiplier_and_currency() -> None:
    notional = Price(Decimal("250.50"), "INR").notional(Quantity(Decimal("100")), multiplier=50)
    assert notional == Money.of("1252500", "INR")


def test_price_must_be_positive() -> None:
    with pytest.raises(UnitError):
        Price(Decimal("0"), "USD")


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ClockError, match="naive"):
        utc(datetime(2026, 11, 8, 9, 15))


def test_aware_datetime_normalised_to_utc() -> None:
    ist_open = datetime(2026, 11, 9, 9, 15, tzinfo=IST)
    assert utc(ist_open) == datetime(2026, 11, 9, 3, 45, tzinfo=timezone.utc)


def test_instants_must_be_ordered() -> None:
    t0 = datetime(2026, 11, 9, 3, 45, tzinfo=timezone.utc)
    Instants(t0, t0 + timedelta(seconds=1), t0 + timedelta(seconds=2), t0 + timedelta(seconds=3))
    with pytest.raises(ClockError, match="out of order"):
        # decision before availability: a decision may not use information not yet available
        Instants(t0, t0, t0 + timedelta(seconds=5), t0 + timedelta(seconds=1))


def test_future_timestamp_refused() -> None:
    now = datetime(2026, 11, 8, tzinfo=timezone.utc)
    with pytest.raises(ClockError, match="after the reference clock"):
        assert_not_future(now + timedelta(minutes=1), now)


def test_date_import_is_used() -> None:
    assert date(2026, 11, 8).isoformat() == "2026-11-08"
