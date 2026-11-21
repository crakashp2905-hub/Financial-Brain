from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from financial_brain.kernel.closures_n import (
    DataStatus,
    DedupeLedger,
    FrozenSnapshot,
    MarketCalendar,
    causal_violations,
    missing_fields,
    status_of,
)


def test_calendar_skips_weekends_and_holidays() -> None:
    cal = MarketCalendar(holidays=frozenset({date(2026, 11, 10)}))
    assert cal.is_session(date(2026, 11, 9)) is True  # Monday
    assert cal.is_session(date(2026, 11, 7)) is False  # Saturday
    assert cal.is_session(date(2026, 11, 10)) is False  # listed holiday
    assert cal.next_session(date(2026, 11, 7)) == date(2026, 11, 9)


def test_asset_class_fields_are_checked_and_unknown_classes_refused() -> None:
    assert missing_fields("FUTURE", {"expiry": "2026-11-26"}) == ["multiplier"]
    assert missing_fields("FX", {"pair": "EURUSD"}) == []
    with pytest.raises(KeyError):
        missing_fields("CRYPTO", {})


def test_data_status_is_live_stale_or_down_by_age() -> None:
    assert status_of(5, 10, 60) is DataStatus.LIVE
    assert status_of(20, 10, 60) is DataStatus.STALE
    assert status_of(70, 10, 60) is DataStatus.DOWN
    with pytest.raises(ValueError):
        status_of(1, 60, 10)


def test_parent_must_appear_before_child() -> None:
    assert causal_violations([("e1", None), ("e2", "e1"), ("e3", "e9")]) == ["e3"]


def test_duplicate_keys_are_reported_not_reapplied() -> None:
    ledger = DedupeLedger()
    assert ledger.add("fill-1") is True
    assert ledger.add("fill-1") is False


def test_snapshot_digest_is_stable_and_immutable() -> None:
    snap = FrozenSnapshot(positions=(("NIFTY", 50),), cash="1000.00")
    assert snap.digest == FrozenSnapshot(positions=(("NIFTY", 50),), cash="1000.00").digest
    assert snap.digest != FrozenSnapshot(positions=(("NIFTY", 51),), cash="1000.00").digest
    with pytest.raises(FrozenInstanceError):
        snap.cash = "0"  # type: ignore[misc]
