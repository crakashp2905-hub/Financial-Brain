from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from financial_brain.kernel.lineage import (
    Holding,
    Listing,
    PredictionRecord,
    SymbolLineage,
    adjust_for_split,
    membership_on,
    reconciles,
)

SHA = "a" * 64


def test_rename_keeps_instrument_identity() -> None:
    lin = SymbolLineage()
    lin.register(Listing("ID1", "OLD", date(2020, 1, 1), date(2024, 6, 30)))
    lin.register(Listing("ID1", "NEW", date(2024, 7, 1), predecessor="ID1"))
    assert lin.resolve("NEW", date(2025, 1, 1)) == "ID1"
    assert lin.resolve("OLD", date(2022, 1, 1)) == "ID1"


def test_unlinked_reuse_of_an_active_symbol_is_refused() -> None:
    lin = SymbolLineage()
    lin.register(Listing("ID1", "ABC", date(2020, 1, 1)))
    with pytest.raises(ValueError, match="unlinked reuse"):
        lin.register(Listing("ID2", "ABC", date(2025, 1, 1)))


def test_delisted_symbol_can_relist_only_after_it_ended() -> None:
    lin = SymbolLineage()
    lin.register(Listing("ID1", "ABC", date(2020, 1, 1), date(2023, 1, 1)))
    lin.register(Listing("ID2", "ABC", date(2024, 1, 1)))
    assert lin.resolve("ABC", date(2022, 6, 1)) == "ID1"
    assert lin.resolve("ABC", date(2024, 6, 1)) == "ID2"


def test_prediction_requires_both_fingerprints() -> None:
    PredictionRecord(Decimal("0.7"), SHA, SHA)
    with pytest.raises(ValueError, match="model fingerprint"):
        PredictionRecord(Decimal("0.7"), "nope", SHA)
    with pytest.raises(ValueError, match="data fingerprint"):
        PredictionRecord(Decimal("0.7"), SHA, "XYZ")


def test_missing_membership_is_flagged_not_filled_with_present_day() -> None:
    snaps = {date(2024, 1, 1): frozenset({"A", "B"})}
    assert membership_on(snaps, date(2023, 6, 1), 30).flag == "MISSING"
    assert membership_on(snaps, date(2023, 6, 1), 30).members == frozenset()
    assert membership_on(snaps, date(2024, 1, 15), 30).flag == "OK"
    stale = membership_on(snaps, date(2024, 6, 1), 30)
    assert stale.flag == "STALE" and stale.as_of == date(2024, 1, 1)


def test_split_preserves_notional_and_reconciles_to_ledger() -> None:
    before = Holding(Decimal("100"), Decimal("50"))
    after = adjust_for_split(before, Decimal("2"))
    assert after == Holding(Decimal("200"), Decimal("25"))
    assert reconciles(before, after, Decimal("5000"))
    assert not reconciles(before, after, Decimal("5001"))
