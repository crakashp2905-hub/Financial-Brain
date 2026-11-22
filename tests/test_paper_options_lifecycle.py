from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import sqlite3

import pytest

from financial_brain.forward_edge.options_risk import OptionLegQuote
from financial_brain.paper.options_lifecycle import (
    OptionLegObservation,
    OptionLifecycleError,
    PaperOptionLifecycle,
)


UTC = timezone.utc
NOW = datetime(2026, 11, 22, 10, tzinfo=UTC)
EXPIRY = date(2026, 12, 31)
EVIDENCE = hashlib.sha256(b"fixture quote evidence").hexdigest()
COST_MODEL = hashlib.sha256(b"frozen India option cost model").hexdigest()


def _observation(leg_id: str, side: str, strike: float, *, token: int,
                 bid: float, ask: float, symbol: str, kind: str = "CE") -> OptionLegObservation:
    leg = OptionLegQuote(leg_id, side, token, symbol, "NIFTY", kind, strike, EXPIRY,
                         75, NOW, bid, ask, 1500, 1500)
    return OptionLegObservation(leg, NOW, "fixture-feed", "NFO", EVIDENCE)


def _plan(ledger: PaperOptionLifecycle, plan_id: str = "spread") -> None:
    ledger.register_plan(
        plan_id=plan_id, account_id="paper-a", decision_at=NOW, quantity=150,
        observations=(
            _observation("long", "BUY", 25000, token=1, bid=100, ask=101,
                         symbol="NIFTY26DEC25000CE"),
            _observation("short", "SELL", 25200, token=2, bid=40, ask=41,
                         symbol="NIFTY26DEC25200CE"),
        ), fee_reserve_inr=Decimal("50"), max_quote_age_seconds=300,
        cost_model_sha256=COST_MODEL,
    )


def _fill(ledger: PaperOptionLifecycle, *, plan_id: str = "spread", fill_id: str,
          leg_id: str, side: str, token: int, symbol: str, quantity: int,
          price: str, bid: str, ask: str, at: datetime,
          bid_qty: int = 1500, ask_qty: int = 1500) -> bool:
    operation = ledger.record_entry_fill if side in {"BUY", "SELL"} else ledger.record_close_fill
    common = dict(plan_id=plan_id, fill_id=fill_id, leg_id=leg_id, quantity=quantity,
                  price=Decimal(price), fees_inr=Decimal("1"), occurred_at=at,
                  quote_observed_at=at - timedelta(seconds=1), quote_available_at=at,
                  bid=Decimal(bid), ask=Decimal(ask), bid_qty=bid_qty, ask_qty=ask_qty,
                  source="fixture-feed", exchange="NFO", evidence_sha256=EVIDENCE,
                  token=token, symbol=symbol, expiry=EXPIRY.isoformat(), lot_size=75)
    if operation is ledger.record_close_fill:
        # Close the frozen entry side; method derives the required opposite side.
        return operation(**common)
    return operation(**common)


def test_partial_leg_fills_are_capped_by_protective_long_and_survive_restart(tmp_path) -> None:
    path = tmp_path / "option-lifecycle.sqlite"
    con = sqlite3.connect(path)
    ledger = PaperOptionLifecycle(con)
    _plan(ledger)
    initial = ledger.state("spread")
    assert initial["strategy_type"] == "DEBIT_VERTICAL"
    assert initial["cash_reserve_inr"] == "9200"
    assert initial["margin_status"] == "NOT_VERIFIED"

    with pytest.raises(OptionLifecycleError, match="protective long"):
        _fill(ledger, fill_id="s0", leg_id="short", side="SELL", token=2,
              symbol="NIFTY26DEC25200CE", quantity=75, price="40", bid="40", ask="41", at=NOW + timedelta(seconds=2))

    _fill(ledger, fill_id="l1", leg_id="long", side="BUY", token=1,
          symbol="NIFTY26DEC25000CE", quantity=75, price="101", bid="100", ask="101", at=NOW + timedelta(seconds=2))
    with pytest.raises(OptionLifecycleError, match="protective long"):
        _fill(ledger, fill_id="s1-too-large", leg_id="short", side="SELL", token=2,
              symbol="NIFTY26DEC25200CE", quantity=150, price="40", bid="40", ask="41", at=NOW + timedelta(seconds=3))
    _fill(ledger, fill_id="s1", leg_id="short", side="SELL", token=2,
          symbol="NIFTY26DEC25200CE", quantity=75, price="40", bid="40", ask="41", at=NOW + timedelta(seconds=3))
    _fill(ledger, fill_id="l2", leg_id="long", side="BUY", token=1,
          symbol="NIFTY26DEC25000CE", quantity=75, price="101", bid="100", ask="101", at=NOW + timedelta(seconds=4))
    _fill(ledger, fill_id="s2", leg_id="short", side="SELL", token=2,
          symbol="NIFTY26DEC25200CE", quantity=75, price="40", bid="40", ask="41", at=NOW + timedelta(seconds=5))
    assert ledger.state("spread")["status"] == "OPEN"
    assert ledger.state("spread")["fee_reserve_remaining_inr"] == "46"
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        con.execute("UPDATE option_lifecycle_events SET event_json='{}' WHERE plan_id='spread'")
    con.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        con.execute("DELETE FROM option_lifecycle_events WHERE plan_id='spread'")
    con.rollback()
    con.close()

    # Recovery rebuilds quantities from the append-only journal; an exact replay is idempotent.
    con = sqlite3.connect(path)
    recovered = PaperOptionLifecycle(con)
    state = recovered.state("spread")
    assert state["open_quantity_by_leg"] == {"long": 150, "short": 150}
    assert state["event_count"] == 5
    assert _fill(recovered, fill_id="s2", leg_id="short", side="SELL", token=2,
                 symbol="NIFTY26DEC25200CE", quantity=75, price="40", bid="40", ask="41",
                 at=NOW + timedelta(seconds=5)) is False
    with pytest.raises(OptionLifecycleError, match="conflicts"):
        _fill(recovered, fill_id="s2", leg_id="short", side="SELL", token=2,
              symbol="NIFTY26DEC25200CE", quantity=75, price="39", bid="40", ask="41",
              at=NOW + timedelta(seconds=5))
    con.close()


def test_partial_closes_cannot_remove_protection_from_open_short(tmp_path) -> None:
    ledger = PaperOptionLifecycle(sqlite3.connect(tmp_path / "close.sqlite"))
    _plan(ledger)
    for leg_id, side, token, symbol, price, bid, ask in [
        ("long", "BUY", 1, "NIFTY26DEC25000CE", "101", "100", "101"),
        ("short", "SELL", 2, "NIFTY26DEC25200CE", "40", "40", "41"),
    ]:
        _fill(ledger, fill_id="entry-" + leg_id, leg_id=leg_id, side=side,
              token=token, symbol=symbol, quantity=150, price=price, bid=bid, ask=ask,
              at=NOW + timedelta(seconds=2 if side == "BUY" else 3))
    with pytest.raises(OptionLifecycleError, match="uncovered short"):
        ledger.record_close_fill(plan_id="spread", fill_id="bad-long-close", leg_id="long",
            quantity=75, price=Decimal("99"), fees_inr=Decimal("1"),
            occurred_at=NOW + timedelta(seconds=4), quote_observed_at=NOW + timedelta(seconds=3),
            quote_available_at=NOW + timedelta(seconds=4), bid=Decimal("99"), ask=Decimal("100"),
            bid_qty=1500, ask_qty=1500, source="fixture-feed", exchange="NFO",
            evidence_sha256=EVIDENCE, token=1, symbol="NIFTY26DEC25000CE",
            expiry=EXPIRY.isoformat(), lot_size=75)

    # Close short risk first, then close the matching long; both legs can be partial.
    ledger.record_close_fill(plan_id="spread", fill_id="short-close", leg_id="short",
        quantity=75, price=Decimal("41"), fees_inr=Decimal("1"),
        occurred_at=NOW + timedelta(seconds=4), quote_observed_at=NOW + timedelta(seconds=3),
        quote_available_at=NOW + timedelta(seconds=4), bid=Decimal("40"), ask=Decimal("41"),
        bid_qty=1500, ask_qty=1500, source="fixture-feed", exchange="NFO",
        evidence_sha256=EVIDENCE, token=2, symbol="NIFTY26DEC25200CE",
        expiry=EXPIRY.isoformat(), lot_size=75)
    ledger.record_close_fill(plan_id="spread", fill_id="long-close", leg_id="long",
        quantity=75, price=Decimal("99"), fees_inr=Decimal("1"),
        occurred_at=NOW + timedelta(seconds=5), quote_observed_at=NOW + timedelta(seconds=4),
        quote_available_at=NOW + timedelta(seconds=5), bid=Decimal("99"), ask=Decimal("100"),
        bid_qty=1500, ask_qty=1500, source="fixture-feed", exchange="NFO",
        evidence_sha256=EVIDENCE, token=1, symbol="NIFTY26DEC25000CE",
        expiry=EXPIRY.isoformat(), lot_size=75)
    state = ledger.state("spread")
    assert state["status"] == "PARTIALLY_OPEN"
    assert state["open_quantity_by_leg"] == {"long": 75, "short": 75}
    assert Decimal(state["net_cashflow_inr"]) == Decimal("-4804")


def test_expiry_is_scenario_only_and_open_positions_remain_unresolved(tmp_path) -> None:
    ledger = PaperOptionLifecycle(sqlite3.connect(tmp_path / "expiry.sqlite"))
    _plan(ledger, "expiry")
    _fill(ledger, plan_id="expiry", fill_id="expiry-long", leg_id="long", side="BUY",
          token=1, symbol="NIFTY26DEC25000CE", quantity=75, price="101", bid="100",
          ask="101", at=NOW + timedelta(seconds=2))
    _fill(ledger, plan_id="expiry", fill_id="expiry-short", leg_id="short", side="SELL",
          token=2, symbol="NIFTY26DEC25200CE", quantity=75, price="40", bid="40",
          ask="41", at=NOW + timedelta(seconds=3))
    with pytest.raises(OptionLifecycleError, match="before the contract expiry"):
        ledger.record_expiry_scenario(plan_id="expiry", event_id="early",
            occurred_at=NOW + timedelta(days=1), underlying_price=Decimal("25100"),
            settlement_mode="PHYSICAL_DELIVERY")
    ledger.record_expiry_scenario(plan_id="expiry", event_id="expiry-day",
        occurred_at=datetime(2026, 12, 31, 10, tzinfo=UTC),
        underlying_price=Decimal("25100"), settlement_mode="PHYSICAL_DELIVERY")
    state = ledger.state("expiry")
    assert state["status"] == "OPEN_AT_EXPIRY_UNRESOLVED"
    assert state["margin_status"] == "NOT_VERIFIED"


def test_stale_or_wrong_contract_quote_cannot_create_a_fill(tmp_path) -> None:
    ledger = PaperOptionLifecycle(sqlite3.connect(tmp_path / "quote-integrity.sqlite"))
    _plan(ledger)
    with pytest.raises(OptionLifecycleError, match="stale"):
        ledger.record_entry_fill(plan_id="spread", fill_id="stale", leg_id="long",
            quantity=75, price=Decimal("101"), fees_inr=Decimal("1"),
            occurred_at=NOW + timedelta(seconds=500), quote_observed_at=NOW,
            quote_available_at=NOW + timedelta(seconds=1), bid=Decimal("100"), ask=Decimal("101"),
            bid_qty=1500, ask_qty=1500, source="fixture-feed", exchange="NFO",
            evidence_sha256=EVIDENCE, token=1, symbol="NIFTY26DEC25000CE",
            expiry=EXPIRY.isoformat(), lot_size=75)
    with pytest.raises(OptionLifecycleError, match="identity"):
        ledger.record_entry_fill(plan_id="spread", fill_id="wrong-contract", leg_id="long",
            quantity=75, price=Decimal("101"), fees_inr=Decimal("1"),
            occurred_at=NOW + timedelta(seconds=2), quote_observed_at=NOW,
            quote_available_at=NOW + timedelta(seconds=1), bid=Decimal("100"), ask=Decimal("101"),
            bid_qty=1500, ask_qty=1500, source="fixture-feed", exchange="NFO",
            evidence_sha256=EVIDENCE, token=999, symbol="NIFTY26DEC25000CE",
            expiry=EXPIRY.isoformat(), lot_size=75)
    assert ledger.state("spread")["open_quantity_by_leg"] == {"long": 0, "short": 0}


def test_cancelled_leg_rejects_late_fill_and_cancel_replay_is_idempotent(tmp_path) -> None:
    ledger = PaperOptionLifecycle(sqlite3.connect(tmp_path / "cancel.sqlite"))
    _plan(ledger)
    at = NOW + timedelta(seconds=1)
    assert ledger.cancel_entry(plan_id="spread", event_id="cancel-long", leg_id="long",
                               occurred_at=at, reason="signal expired") is True
    assert ledger.cancel_entry(plan_id="spread", event_id="cancel-long", leg_id="long",
                               occurred_at=at, reason="signal expired") is False
    assert ledger.state("spread")["status"] == "CANCELLED_UNFILLED"
    with pytest.raises(OptionLifecycleError, match="cancelled"):
        _fill(ledger, fill_id="late", leg_id="long", side="BUY", token=1,
              symbol="NIFTY26DEC25000CE", quantity=75, price="101", bid="100",
              ask="101", at=NOW + timedelta(seconds=2))
