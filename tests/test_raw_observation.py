from datetime import date, datetime, timedelta, timezone
import hashlib

import pytest

from financial_brain.forward_edge.data_permissions import DataPermission, DataUse, PermissionStatus
from financial_brain.forward_edge.raw_observation import RawObservation, RawObservationError


NOW = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
RAW = b'{"price":100.25,"quantity":3}'


def _permission(status=PermissionStatus.UNKNOWN_RIGHTS):
    return DataPermission(
        source_id="KITE", use=DataUse.PAPER_SIMULATION, status=status,
        effective_from=date(2026, 1, 1) if status is PermissionStatus.USE_ALLOWED else None,
        effective_through=None, evidence_ref="owner-rights-record", reviewer="owner"
        if status is PermissionStatus.USE_ALLOWED else None,
    )


def _observation(**changes):
    values = {
        "source_id": "KITE", "instrument_id": "NSE:INFY",
        "exchange_ts": NOW, "received_ts": NOW + timedelta(milliseconds=50),
        "available_at": NOW + timedelta(milliseconds=75),
        "published_at": NOW, "revision_id": "quote-42", "raw_payload": RAW,
        "entitlement": _permission(),
    }
    values.update(changes)
    return RawObservation(**values)


def test_raw_observation_retains_dual_clocks_revision_rights_and_exact_checksum():
    event = _observation()
    assert event.exchange_ts == NOW
    assert event.available_at == NOW + timedelta(milliseconds=75)
    assert event.revision_id == "quote-42"
    assert event.checksum == hashlib.sha256(RAW).hexdigest()
    assert event.entitlement.status is PermissionStatus.UNKNOWN_RIGHTS
    decoded = event.payload
    decoded["price"] = 0
    assert event.payload["price"] == 100.25
    assert event.as_dict()["raw_payload_hex"] == RAW.hex()


def test_raw_observation_accepts_exchange_clock_skew_without_rewriting_source_time():
    observation = _observation(exchange_ts=NOW + timedelta(seconds=1))
    assert observation.exchange_ts > observation.received_ts
    assert observation.received_ts <= observation.available_at


def test_raw_observation_rejects_bad_checksum_and_invalid_availability_order():
    with pytest.raises(RawObservationError, match="checksum does not match"):
        _observation(checksum="0" * 64)
    with pytest.raises(RawObservationError, match="before it was received"):
        _observation(available_at=NOW)
    with pytest.raises(RawObservationError, match="before publication"):
        _observation(published_at=NOW + timedelta(seconds=1))


def test_raw_observation_requires_matching_source_permission_and_immutable_bytes():
    mismatched = DataPermission(
        source_id="OTHER", use=DataUse.PAPER_SIMULATION, status=PermissionStatus.UNKNOWN_RIGHTS,
        effective_from=None, effective_through=None, evidence_ref="record", reviewer=None,
    )
    with pytest.raises(RawObservationError, match="must match"):
        _observation(entitlement=mismatched)
    with pytest.raises(RawObservationError, match="immutable bytes"):
        _observation(raw_payload={"price": 1})
