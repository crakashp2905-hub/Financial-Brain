from datetime import date
from decimal import Decimal

import pytest

from financial_brain.forward_edge.instrument_rules import (
    InstrumentRuleBook,
    InstrumentRuleError,
    fixture_rule_book,
)


def test_rulebook_resolves_latest_rule_at_or_before_session_date():
    data = fixture_rule_book("NSE:INFY", effective_from=date(2026, 1, 1))
    data["instruments"]["NSE:INFY"].append({
        "effective_from": "2026-11-10", "tick_size": "0.10", "lot_size": 5,
        "freeze_quantity": 50, "expiry": None, "status": "ACTIVE",
        "session_phases": ["CONTINUOUS"],
    })
    book = InstrumentRuleBook(data)

    prior = book.resolve("NSE:INFY", date(2026, 11, 9))
    current = book.resolve("NSE:INFY", date(2026, 11, 13))
    assert prior.tick_size == Decimal("0.05") and prior.lot_size == 1
    assert current.tick_size == Decimal("0.10") and current.lot_size == 5
    assert current.quantity_unit == "UNSPECIFIED"
    with pytest.raises(InstrumentRuleError, match="quantity unit is unknown"):
        book.validate_order(instrument="NSE:INFY", day=date(2026, 11, 13),
                            session_phase="CONTINUOUS", quantity=5,
                            limit_price=Decimal("100.10"), tick_size=Decimal("0.10"),
                            quantity_unit="OPTION_CONTRACTS")


def test_rulebook_refuses_unknown_instruments_and_dates_before_first_rule():
    book = InstrumentRuleBook(fixture_rule_book("NSE:INFY", effective_from=date(2026, 1, 1)))
    with pytest.raises(InstrumentRuleError, match="no instrument rules"):
        book.resolve("NSE:TCS", date(2026, 11, 13))
    with pytest.raises(InstrumentRuleError, match="no instrument rule effective"):
        book.resolve("NSE:INFY", date(2025, 12, 31))


@pytest.mark.parametrize("kwargs, message", [
    ({"quantity": 3}, "multiple of lot size 5"),
    ({"quantity": 55}, "freeze limit 50"),
    ({"tick_size": Decimal("0.05")}, "differs from effective instrument rule"),
    ({"limit_price": Decimal("100.05")}, "violates effective tick size"),
    ({"session_phase": "AUCTION"}, "not eligible during AUCTION"),
])
def test_dated_order_rules_refuse_invalid_ticket_without_repair(kwargs, message):
    book = InstrumentRuleBook(fixture_rule_book(
        "NSE:INFY", tick_size="0.10", lot_size=5, freeze_quantity=50))
    arguments = {"instrument": "NSE:INFY", "day": date(2026, 11, 13),
                 "session_phase": "CONTINUOUS", "quantity": 5,
                 "limit_price": Decimal("100.10"), "tick_size": Decimal("0.10")}
    arguments.update(kwargs)
    with pytest.raises(InstrumentRuleError, match=message):
        book.validate_order(**arguments)


def test_status_and_expiry_are_applied_as_of_requested_date():
    suspended = InstrumentRuleBook(fixture_rule_book("NSE:INFY", status="SUSPENDED"))
    with pytest.raises(InstrumentRuleError, match="status SUSPENDED"):
        suspended.validate_order(instrument="NSE:INFY", day=date(2026, 11, 13),
                                 session_phase="CONTINUOUS", quantity=1,
                                 limit_price=Decimal("100.00"), tick_size=Decimal("0.05"))

    expired = InstrumentRuleBook(fixture_rule_book("NSE:INFY", expiry=date(2026, 11, 12)))
    with pytest.raises(InstrumentRuleError, match="expired"):
        expired.validate_order(instrument="NSE:INFY", day=date(2026, 11, 13),
                               session_phase="CONTINUOUS", quantity=1,
                               limit_price=Decimal("100.00"), tick_size=Decimal("0.05"))


def test_option_quantity_uses_contract_units_and_effective_lot_size():
    book = InstrumentRuleBook(fixture_rule_book(
        "NFO:NIFTY-2026-12-31-26000-CE", lot_size=25,
        quantity_unit="OPTION_CONTRACTS", freeze_quantity=250,
    ))
    arguments = {
        "instrument": "NFO:NIFTY-2026-12-31-26000-CE", "day": date(2026, 11, 13),
        "session_phase": "CONTINUOUS", "quantity": 50,
        "limit_price": Decimal("100.00"), "tick_size": Decimal("0.05"),
        "quantity_unit": "OPTION_CONTRACTS",
    }
    rule = book.validate_order(**arguments)
    assert rule.quantity_unit == "OPTION_CONTRACTS" and rule.lot_size == 25

    with pytest.raises(InstrumentRuleError, match="multiple of lot size 25"):
        book.validate_order(**(arguments | {"quantity": 1}))
    with pytest.raises(InstrumentRuleError, match="differs from effective instrument unit"):
        book.validate_order(**(arguments | {"quantity_unit": "SHARES"}))
