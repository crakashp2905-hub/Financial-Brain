from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from financial_brain.forward_edge.instrument_master import (
    InstrumentAdjustment,
    InstrumentMaster,
    InstrumentMasterError,
    InstrumentRecord,
    NoOptionTrade,
)


PUBLISHED = datetime(2026, 1, 1, 9, tzinfo=timezone.utc)
RECEIVED = datetime(2026, 1, 1, 9, 0, 1, tzinfo=timezone.utc)


def record(**changes):
    values = {
        "canonical_id": "NSE:ABC", "kind": "EQUITY", "source_id": "KITE_CONNECT_READ_ONLY",
        "source_token": "101", "exchange": "NSE", "segment": "NSE_EQ",
        "trading_symbol": "ABC", "effective_from": date(2020, 1, 1),
        "effective_through": None, "source_published_at": PUBLISHED, "received_at": RECEIVED,
        "source_payload_sha256": "a" * 64, "lot_size": 1, "tick_size": Decimal("0.05"),
        "underlying_id": None, "isin": "INE000A01000", "expiry": None,
        "strike": None, "right": None, "adjustment_factor": Decimal("1"),
    }
    values.update(changes)
    return InstrumentRecord(**values)


def option(**changes):
    values = {
        "canonical_id": "NSE:ABC:2026-01-29:100:CE", "kind": "OPTION",
        "source_id": "KITE_CONNECT_READ_ONLY", "source_token": "9001", "exchange": "NSE",
        "segment": "NFO_OPT", "trading_symbol": "ABC26JAN100CE",
        "effective_from": date(2026, 1, 1), "effective_through": date(2026, 1, 29),
        "source_published_at": PUBLISHED, "received_at": RECEIVED,
        "source_payload_sha256": "b" * 64, "lot_size": 75, "tick_size": Decimal("0.05"),
        "underlying_id": "NSE:ABC", "isin": None, "expiry": date(2026, 1, 29),
        "strike": Decimal("100"), "right": "CE", "adjustment_factor": Decimal("1"),
    }
    values.update(changes)
    return InstrumentRecord(**values)


def test_option_resolution_requires_exact_source_listed_contract():
    master = InstrumentMaster([record(), option()])
    found = master.resolve_option(source_id="KITE_CONNECT_READ_ONLY", underlying_id="NSE:ABC",
                                  expiry=date(2026, 1, 29), strike=Decimal("100"), right="CE",
                                  as_of=date(2026, 1, 12))
    assert found.source_token == "9001"
    with pytest.raises(NoOptionTrade, match="NO_OPTION_TRADE"):
        master.resolve_option(source_id="KITE_CONNECT_READ_ONLY", underlying_id="NSE:ABC",
                              expiry=date(2026, 1, 29), strike=Decimal("105"), right="CE",
                              as_of=date(2026, 1, 12))
    with pytest.raises(NoOptionTrade, match="expired"):
        master.resolve_option(source_id="KITE_CONNECT_READ_ONLY", underlying_id="NSE:ABC",
                              expiry=date(2026, 1, 29), strike=Decimal("100"), right="CE",
                              as_of=date(2026, 1, 30))


def test_source_token_resolves_effective_dated_isin_history_without_overwrite():
    old = record(effective_from=date(2020, 1, 1), effective_through=date(2022, 12, 31),
                 isin="INE000A01000")
    new = record(effective_from=date(2023, 1, 1), isin="INE000A01018")
    master = InstrumentMaster([new, old])
    assert master.resolve_token(source_id=old.source_id, exchange="NSE", source_token="101",
                               as_of=date(2022, 12, 1)).isin == "INE000A01000"
    assert master.resolve_token(source_id=new.source_id, exchange="NSE", source_token="101",
                               as_of=date(2024, 1, 1)).isin == "INE000A01018"


def test_index_candidate_reconciliation_marks_unavailable_underlying_no_trade():
    master = InstrumentMaster([record(), option()])
    rows = master.reconcile_optionable_underlyings(
        source_id="KITE_CONNECT_READ_ONLY", candidates=("NSE:ABC", "NSE:XYZ", "NSE:ABC"),
        as_of=date(2026, 1, 12))
    assert [(row["underlying_id"], row["status"]) for row in rows] == [
        ("NSE:ABC", "OPTIONABLE"), ("NSE:XYZ", "NO_OPTION_TRADE")]
    expired = master.reconcile_optionable_underlyings(
        source_id="KITE_CONNECT_READ_ONLY", candidates=("NSE:ABC",), as_of=date(2026, 1, 30))
    assert expired[0]["status"] == "NO_OPTION_TRADE"


def test_overlapping_token_history_is_rejected():
    old = record(effective_through=date(2023, 3, 1))
    new = record(effective_from=date(2023, 3, 1), isin="INE000A01018")
    with pytest.raises(InstrumentMasterError, match="overlapping effective identity"):
        InstrumentMaster([old, new])


def test_master_round_trip_is_canonical_and_hash_bound():
    master = InstrumentMaster([record(), option()])
    restored = InstrumentMaster.from_dict(master.as_dict())
    assert restored.digest == master.digest
    tampered = master.as_dict()
    tampered["records"][0]["source_token"] = "forged"
    with pytest.raises(InstrumentMasterError, match="content hash"):
        InstrumentMaster.from_dict(tampered)


def test_master_document_matches_the_published_json_schema():
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "docs/edge-program/instrument-master.v1.schema.json").read_text(
        encoding="utf-8"))
    master = InstrumentMaster([record(), option()])
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(master.as_dict())


def test_adjustments_are_source_evidenced_and_effective_dated():
    action = InstrumentAdjustment(
        canonical_id="NSE:ABC", action_id="split-2-for-1", action_type="SPLIT",
        effective_on=date(2026, 2, 1), price_factor=Decimal("0.5"), volume_factor=Decimal("2"),
        source_id="NSE_CORPORATE_ACTIONS", source_published_at=PUBLISHED, received_at=RECEIVED,
        source_payload_sha256="c" * 64,
    )
    master = InstrumentMaster([record()], [action])
    assert master.adjustment_factors(canonical_id="NSE:ABC", as_of=date(2026, 1, 31)) == (
        Decimal("1"), Decimal("1"))
    assert master.adjustment_factors(canonical_id="NSE:ABC", as_of=date(2026, 2, 1)) == (
        Decimal("0.5"), Decimal("2"))


@pytest.mark.parametrize("changes,match", [
    ({"source_published_at": datetime(2026, 1, 1, 9)}, "timezone-aware"),
    ({"received_at": datetime(2025, 12, 31, 23, tzinfo=timezone.utc)}, "cannot follow local receipt"),
    ({"source_payload_sha256": "bad"}, "SHA-256"),
    ({"kind": "OPTION", "underlying_id": None, "expiry": None}, "underlying and expiry"),
    ({"kind": "OPTION", "underlying_id": "NSE:ABC", "expiry": date(2026, 1, 29),
      "strike": None, "right": "CE"}, "listed strike"),
])
def test_invalid_identity_or_provenance_is_rejected(changes, match):
    with pytest.raises(InstrumentMasterError, match=match):
        record(**changes)
