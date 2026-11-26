from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from financial_brain.paper.derivatives_subledger import (
    DerivativesLedgerError, DerivativesSubledger, OptionContract, OptionLegOrder,
)


NOW = datetime(2026, 11, 20, 10, tzinfo=timezone.utc)
HASH = "a" * 64
EXPIRY_CUTOFF = datetime(2026, 11, 26, 15, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
CALENDAR_HASH = "c" * 64


def _contract(contract_id, strike, *, underlying="NIFTY", settlement="CASH",
              expiry=date(2026, 11, 26), option_type="CE"):
    return OptionContract(contract_id, underlying, option_type, Decimal(str(strike)), expiry,
                          50, settlement, HASH)


def _legs(*, settlement="CASH", quantity=100):
    underlying = "RELIANCE" if settlement == "PHYSICAL" else "NIFTY"
    return (
        OptionLegOrder("long", _contract(f"{underlying}-100-CE", 100, underlying=underlying,
                                          settlement=settlement), "BUY", quantity),
        OptionLegOrder("short", _contract(f"{underlying}-110-CE", 110, underlying=underlying,
                                           settlement=settlement), "SELL", quantity),
    )


def _open(db, *, package_id="spread", settlement="CASH", quantity=100):
    legs = _legs(settlement=settlement, quantity=quantity)
    db.create_package(package_id=package_id, account_id="paper-1", structure="DEBIT_VERTICAL",
                      legs=legs, created_at=NOW - timedelta(days=1))
    return legs


def _fill(db, *, package_id="spread", leg_id="long", event_id="fill-long", quantity=50,
          price="10", at=NOW):
    db.record_fill(
        package_id=package_id, event_id=event_id, leg_id=leg_id,
        side="BUY" if leg_id == "long" else "SELL", quantity=quantity,
        price=Decimal(price), occurred_at=at, quote_event_id=f"quote-{event_id}",
        quote_observed_at=at - timedelta(seconds=2),
        quote_available_at=at - timedelta(seconds=1), quote_evidence_sha256=HASH,
    )


def test_unmatched_partial_option_spread_is_unhedged_and_blocks_deployment(tmp_path):
    db = DerivativesSubledger(tmp_path / "derivatives.sqlite3")
    _open(db)
    _fill(db, quantity=50)
    state = db.snapshot("spread")
    assert state["status"] == "UNHEDGED_PARTIAL"
    assert state["defined_risk"] is False
    assert state["capital_deployment_allowed"] is False
    assert state["leg_filled_quantities"] == {"long": 50, "short": 0}
    db.close()


def test_balanced_partial_spread_becomes_defined_risk_and_replays_after_restart(tmp_path):
    path = tmp_path / "derivatives.sqlite3"
    db = DerivativesSubledger(path)
    _open(db)
    _fill(db, quantity=50, price="10")
    _fill(db, leg_id="short", event_id="fill-short", quantity=50, price="4")
    state = db.snapshot("spread")
    assert state["status"] == "BALANCED_PARTIAL"
    assert state["defined_risk"] and state["capital_deployment_allowed"]
    assert state["premium_cash_flow_inr"] == Decimal("-300")
    db.close()

    resumed = DerivativesSubledger(path)
    assert resumed.snapshot("spread") == state
    resumed.close()


def test_full_cash_settled_spread_emits_source_bound_per_contract_expiry_flows(tmp_path):
    db = DerivativesSubledger(tmp_path / "derivatives.sqlite3")
    _open(db, quantity=50)
    _fill(db, quantity=50, price="10")
    _fill(db, leg_id="short", event_id="fill-short", quantity=50, price="4")
    published = datetime(2026, 11, 27, 10, tzinfo=timezone.utc)
    assert db.settle_expiry(
        package_id="spread", contract_id="NIFTY-100-CE", event_id="settle-long",
        settlement_date=date(2026, 11, 26), expiry_cutoff_at=EXPIRY_CUTOFF,
        expiry_calendar_sha256=CALENDAR_HASH, settlement_at=published + timedelta(minutes=1),
        published_at=published, underlying_price=Decimal("115"), source_id="official-settlement",
        source_sha256="b" * 64,
    )
    assert db.snapshot("spread")["status"] == "PARTIALLY_SETTLED"
    assert db.settle_expiry(
        package_id="spread", contract_id="NIFTY-110-CE", event_id="settle-short",
        settlement_date=date(2026, 11, 26), expiry_cutoff_at=EXPIRY_CUTOFF,
        expiry_calendar_sha256=CALENDAR_HASH, settlement_at=published + timedelta(minutes=1),
        published_at=published, underlying_price=Decimal("115"), source_id="official-settlement",
        source_sha256="b" * 64,
    )
    state = db.snapshot("spread")
    assert state["status"] == "SETTLED"
    assert state["settlement_status"] == "SETTLED"
    legs = {row["contract_id"]: row for event in state["settlements"] for row in event["legs"]}
    assert legs["NIFTY-100-CE"]["cash_flow_inr"] == "750"
    assert legs["NIFTY-100-CE"]["gross_pnl_before_charges_inr"] == "250"
    assert legs["NIFTY-110-CE"]["cash_flow_inr"] == "-250"
    assert legs["NIFTY-110-CE"]["gross_pnl_before_charges_inr"] == "-50"
    assert state["defined_risk"] is False
    assert not db.settle_expiry(
        package_id="spread", contract_id="NIFTY-110-CE", event_id="settle-short",
        settlement_date=date(2026, 11, 26), expiry_cutoff_at=EXPIRY_CUTOFF,
        expiry_calendar_sha256=CALENDAR_HASH, settlement_at=published + timedelta(minutes=1),
        published_at=published, underlying_price=Decimal("115"), source_id="official-settlement",
        source_sha256="b" * 64,
    )
    with pytest.raises(DerivativesLedgerError, match="conflicts"):
        db.settle_expiry(
            package_id="spread", contract_id="NIFTY-110-CE", event_id="settle-short",
            settlement_date=date(2026, 11, 26), expiry_cutoff_at=EXPIRY_CUTOFF,
            expiry_calendar_sha256=CALENDAR_HASH, settlement_at=published + timedelta(minutes=1),
            published_at=published, underlying_price=Decimal("116"), source_id="official-settlement",
            source_sha256="b" * 64,
        )
    db.close()


def test_physical_call_assignment_records_strike_cash_and_underlying_units(tmp_path):
    db = DerivativesSubledger(tmp_path / "physical.sqlite3")
    _open(db, settlement="PHYSICAL", quantity=50)
    _fill(db, quantity=50, price="10")
    _fill(db, leg_id="short", event_id="fill-short", quantity=50, price="4")
    published = datetime(2026, 11, 27, 10, tzinfo=timezone.utc)
    db.settle_expiry(
        package_id="spread", contract_id="RELIANCE-100-CE", event_id="settle-long",
        settlement_date=date(2026, 11, 26), expiry_cutoff_at=EXPIRY_CUTOFF,
        expiry_calendar_sha256=CALENDAR_HASH, settlement_at=published + timedelta(minutes=1),
        published_at=published, underlying_price=Decimal("115"), source_id="official-settlement",
        source_sha256="b" * 64,
    )
    row = db.snapshot("spread")["settlements"][0]["legs"][0]
    assert row["cash_flow_inr"] == "-5000"
    assert row["underlying_units"] == 50
    assert row["settlement_value_inr"] == "750"
    db.close()


def test_physical_put_assignment_records_delivery_and_cash_obligations(tmp_path):
    db = DerivativesSubledger(tmp_path / "physical-put.sqlite3")
    expiry = date(2026, 11, 26)
    legs = (
        OptionLegOrder("long-put", _contract("RELIANCE-110-PE", 110, underlying="RELIANCE",
                                              option_type="PE", settlement="PHYSICAL", expiry=expiry),
                       "BUY", 50),
        OptionLegOrder("short-put", _contract("RELIANCE-100-PE", 100, underlying="RELIANCE",
                                               option_type="PE", settlement="PHYSICAL", expiry=expiry),
                       "SELL", 50),
    )
    db.create_package(package_id="put-spread", account_id="paper-1", structure="DEBIT_VERTICAL",
                      legs=legs, created_at=NOW - timedelta(days=1))
    for leg, price in zip(legs, ("10", "4"), strict=True):
        at = NOW
        db.record_fill(package_id="put-spread", event_id=f"fill-{leg.leg_id}", leg_id=leg.leg_id,
                       side=leg.side, quantity=50, price=Decimal(price), occurred_at=at,
                       quote_event_id=f"quote-{leg.leg_id}", quote_observed_at=at - timedelta(seconds=2),
                       quote_available_at=at - timedelta(seconds=1), quote_evidence_sha256=HASH)
    published = datetime(2026, 11, 27, 10, tzinfo=timezone.utc)
    db.settle_expiry(
        package_id="put-spread", contract_id="RELIANCE-110-PE", event_id="put-long-settle",
        settlement_date=expiry, expiry_cutoff_at=EXPIRY_CUTOFF, expiry_calendar_sha256=CALENDAR_HASH,
        settlement_at=published + timedelta(minutes=1), published_at=published,
        underlying_price=Decimal("90"), source_id="official-settlement", source_sha256="b" * 64,
    )
    db.settle_expiry(
        package_id="put-spread", contract_id="RELIANCE-100-PE", event_id="put-short-settle",
        settlement_date=expiry, expiry_cutoff_at=EXPIRY_CUTOFF, expiry_calendar_sha256=CALENDAR_HASH,
        settlement_at=published + timedelta(minutes=1), published_at=published,
        underlying_price=Decimal("90"), source_id="official-settlement", source_sha256="b" * 64,
    )
    legs_out = {row["contract_id"]: row for event in db.snapshot("put-spread")["settlements"]
                for row in event["legs"]}
    assert legs_out["RELIANCE-110-PE"]["cash_flow_inr"] == "5500"
    assert legs_out["RELIANCE-110-PE"]["underlying_units"] == -50
    assert legs_out["RELIANCE-110-PE"]["gross_pnl_before_charges_inr"] == "500"
    assert legs_out["RELIANCE-100-PE"]["cash_flow_inr"] == "-5000"
    assert legs_out["RELIANCE-100-PE"]["underlying_units"] == 50
    assert legs_out["RELIANCE-100-PE"]["gross_pnl_before_charges_inr"] == "-300"
    db.close()


def test_fill_replay_is_idempotent_but_conflict_and_overfill_are_rejected(tmp_path):
    db = DerivativesSubledger(tmp_path / "fill.sqlite3")
    _open(db, quantity=50)
    kwargs = {"package_id": "spread", "event_id": "fill-long", "leg_id": "long",
              "side": "BUY", "quantity": 50, "price": Decimal("10"), "occurred_at": NOW,
              "quote_event_id": "quote-fill-long", "quote_observed_at": NOW - timedelta(seconds=2),
              "quote_available_at": NOW - timedelta(seconds=1), "quote_evidence_sha256": HASH}
    assert db.record_fill(**kwargs)
    assert not db.record_fill(**kwargs)
    with pytest.raises(DerivativesLedgerError, match="conflicts"):
        db.record_fill(**(kwargs | {"price": Decimal("11")}))
    with pytest.raises(DerivativesLedgerError, match="exceed"):
        db.record_fill(**(kwargs | {"event_id": "overfill", "quantity": 50}))
    db.close()


def test_expiry_settlement_requires_publication_and_cannot_be_revised(tmp_path):
    db = DerivativesSubledger(tmp_path / "settlement.sqlite3")
    _open(db, quantity=50)
    _fill(db, quantity=50)
    before_expiry = datetime(2026, 11, 25, 10, tzinfo=timezone.utc)
    with pytest.raises(DerivativesLedgerError, match="precedes contract expiry"):
        db.settle_expiry(
            package_id="spread", contract_id="NIFTY-100-CE", event_id="settle",
            settlement_date=date(2026, 11, 25), expiry_cutoff_at=EXPIRY_CUTOFF,
            expiry_calendar_sha256=CALENDAR_HASH, settlement_at=before_expiry, published_at=before_expiry,
            underlying_price=Decimal("115"), source_id="settlement", source_sha256="b" * 64,
        )
    before_cutoff = datetime(2026, 11, 26, 14, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    with pytest.raises(DerivativesLedgerError, match="follow the source-hashed expiry cutoff"):
        db.settle_expiry(
            package_id="spread", contract_id="NIFTY-100-CE", event_id="settle-before-cutoff",
            settlement_date=date(2026, 11, 26), expiry_cutoff_at=EXPIRY_CUTOFF,
            expiry_calendar_sha256=CALENDAR_HASH, settlement_at=before_cutoff + timedelta(minutes=1),
            published_at=before_cutoff, underlying_price=Decimal("115"), source_id="settlement",
            source_sha256="b" * 64,
        )
    db.close()


def test_debit_vertical_definition_rejects_inverted_or_mismatched_legs(tmp_path):
    db = DerivativesSubledger(tmp_path / "invalid.sqlite3")
    legs = (OptionLegOrder("long", _contract("long", 110), "BUY", 50),
            OptionLegOrder("short", _contract("short", 100), "SELL", 50))
    with pytest.raises(DerivativesLedgerError, match="inverted"):
        db.create_package(package_id="bad", account_id="paper", structure="DEBIT_VERTICAL",
                          legs=legs, created_at=NOW)
    db.close()
