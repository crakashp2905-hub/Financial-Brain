from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from financial_brain.kernel.guards import (
    Mode,
    ModeError,
    display_quote,
    external_orders_armed,
    prerequisite_gate,
    require_non_live,
)
from financial_brain.kernel.ledger import (
    CREDIT,
    DEBIT,
    DoubleEntryLedger,
    Entry,
    ImbalanceError,
    LedgerError,
    Transaction,
)
from financial_brain.kernel.ticks import TickError, TickRule, TickSchedule
from financial_brain.kernel.units import Money


def _entry(account: str, side: str, amount: str, currency: str = "INR") -> Entry:
    return Entry(account, side, Money.of(amount, currency))


def _buy(txn_id: str, amount: str = "1000") -> Transaction:
    return Transaction(
        txn_id,
        (
            _entry("position:NSE:ABC", DEBIT, amount),
            _entry("cash", CREDIT, amount),
        ),
    )


def test_unbalanced_transaction_is_refused() -> None:
    with pytest.raises(ImbalanceError):
        Transaction("bad", (_entry("cash", DEBIT, "100"), _entry("fees", CREDIT, "99")))


def test_multi_currency_balances_are_checked_per_currency() -> None:
    with pytest.raises(ImbalanceError):
        Transaction(
            "fx",
            (
                _entry("cash:INR", DEBIT, "100", "INR"),
                _entry("cash:USD", CREDIT, "100", "USD"),
            ),
        )


def test_replay_of_identical_transaction_has_no_second_effect() -> None:
    ledger = DoubleEntryLedger()
    assert ledger.post(_buy("t1")) is True
    assert ledger.post(_buy("t1")) is False
    assert ledger.balance("cash", "INR") == Decimal("-1000")
    assert len(ledger) == 1


def test_conflicting_content_under_same_id_is_refused() -> None:
    ledger = DoubleEntryLedger()
    ledger.post(_buy("t1", "1000"))
    with pytest.raises(LedgerError, match="conflicting"):
        ledger.post(_buy("t1", "2000"))


def test_reversal_is_a_new_entry_and_restores_balance() -> None:
    ledger = DoubleEntryLedger()
    ledger.post(_buy("t1"))
    ledger.reverse("t1", new_id="t1-rev", reason="booked in error")
    assert ledger.balance("cash", "INR") == Decimal("0")
    assert len(ledger) == 2
    assert ledger.trial_balance_ok()


def test_reversal_requires_reason_and_cannot_repeat() -> None:
    ledger = DoubleEntryLedger()
    ledger.post(_buy("t1"))
    with pytest.raises(LedgerError, match="reason"):
        ledger.reverse("t1", new_id="r1", reason="")
    ledger.reverse("t1", new_id="r2", reason="correction")
    with pytest.raises(LedgerError, match="already reversed"):
        ledger.reverse("t1", new_id="r3", reason="again")


def test_empty_or_zero_entries_refused() -> None:
    with pytest.raises(LedgerError):
        Transaction("empty", ())
    with pytest.raises(LedgerError):
        Entry("cash", DEBIT, Money.of(0, "INR"))


def test_live_mode_is_terminal_and_refused() -> None:
    assert require_non_live(Mode.PAPER) is Mode.PAPER
    with pytest.raises(ModeError):
        require_non_live(Mode.LIVE_DISABLED)


def test_environment_variable_cannot_arm_external_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVE_ENABLED", "1")
    assert external_orders_armed({"LIVE_ENABLED": "1"}) is False
    assert external_orders_armed({"LIVE_ENABLED": "1"}, approval=object()) is False


def test_missing_or_unknown_prerequisite_abstains() -> None:
    assert prerequisite_gate({}).decision == "NO_TRADE"
    gate = prerequisite_gate({"quote_fresh": True, "tick_known": None, "risk_ok": False})
    assert gate.decision == "NO_TRADE"
    assert gate.missing == ("risk_ok", "tick_known")
    assert prerequisite_gate({"quote_fresh": True, "risk_ok": True}).tradable


def test_absent_quote_never_displays_as_zero() -> None:
    assert display_quote(None) == "NO QUOTE"
    assert display_quote(0.0) == "0.00"


SCHEDULE = TickSchedule(
    [
        TickRule(date(2026, 1, 1), Decimal("0.05")),
        TickRule(date(2026, 6, 1), Decimal("0.10")),
    ]
)


def test_tick_rule_is_effective_dated() -> None:
    assert SCHEDULE.normalize("100.07", date(2026, 3, 2), "NEAREST") == Decimal("100.05")
    assert SCHEDULE.normalize("100.07", date(2026, 7, 1), "NEAREST") == Decimal("100.10")


def test_normalisation_is_idempotent_round_trip() -> None:
    for raw in ("99.99", "100.01", "123.456", "0.17"):
        once = SCHEDULE.normalize(raw, date(2026, 7, 1), "DOWN")
        assert SCHEDULE.normalize(once, date(2026, 7, 1), "DOWN") == once


def test_direction_rounds_toward_the_stated_side() -> None:
    assert SCHEDULE.normalize("100.12", date(2026, 7, 1), "DOWN") == Decimal("100.10")
    assert SCHEDULE.normalize("100.12", date(2026, 7, 1), "UP") == Decimal("100.20")


def test_price_rounding_to_zero_is_refused_not_zeroed() -> None:
    with pytest.raises(TickError, match="rounds to zero"):
        SCHEDULE.normalize("0.07", date(2026, 7, 1), "DOWN")


def test_day_before_first_rule_fails_closed() -> None:
    with pytest.raises(TickError, match="refusing to guess"):
        SCHEDULE.normalize("100", date(2025, 12, 31))


def test_duplicate_effective_dates_and_bad_ticks_rejected() -> None:
    with pytest.raises(TickError):
        TickSchedule([TickRule(date(2026, 1, 1), Decimal("0.05")), TickRule(date(2026, 1, 1), Decimal("0.1"))])
    with pytest.raises(TickError):
        TickRule(date(2026, 1, 1), Decimal("0"))
