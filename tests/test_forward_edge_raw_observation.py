from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from financial_brain.forward_edge.data_permissions import (
    DataPermission,
    DataUse,
    PermissionStatus,
)
from financial_brain.forward_edge.raw_observation import RawObservation, RawObservationError


T0 = datetime(2026, 11, 20, 4, 0, tzinfo=timezone.utc)
PAYLOAD = b'{"bid":100.0,"ask":100.1}'


def permission(source: str = "kite") -> DataPermission:
    return DataPermission(source, DataUse.PAPER_SIMULATION, PermissionStatus.UNKNOWN_RIGHTS,
                          None, None, "terms:unreviewed", None)


def observation(**overrides) -> RawObservation:
    values = {
        "source_id": "kite", "instrument_id": "NSE:INFY",
        "exchange_ts": T0, "received_ts": T0 + timedelta(milliseconds=20),
        "available_at": T0 + timedelta(milliseconds=25),
        "published_at": T0 + timedelta(milliseconds=10),
        "revision_id": "packet-01", "payload": PAYLOAD,
        "entitlement": permission(),
    }
    values.update(overrides)
    return RawObservation.capture(**values)


def test_raw_observation_captures_checksum_clocks_permission_and_round_trips():
    row = observation()
    assert row.checksum == hashlib.sha256(PAYLOAD).hexdigest()
    assert row.available_at == T0 + timedelta(milliseconds=25)
    assert row.entitlement.status is PermissionStatus.UNKNOWN_RIGHTS
    assert RawObservation.from_dict(row.as_dict()) == row


def test_raw_observation_is_frozen_and_payload_is_immutable():
    row = observation()
    with pytest.raises(FrozenInstanceError):
        row.revision_id = "changed"
    with pytest.raises(RawObservationError, match="immutable bytes"):
        observation(payload=bytearray(PAYLOAD))


def test_raw_observation_rejects_bad_digest_clocks_and_permission_binding():
    valid = observation()
    with pytest.raises(RawObservationError, match="checksum does not match"):
        RawObservation(valid.source_id, valid.instrument_id, valid.exchange_ts, valid.received_ts,
                       valid.available_at, valid.published_at, valid.revision_id, "0" * 64,
                       valid.entitlement, valid.payload)
    with pytest.raises(RawObservationError, match="timezone aware"):
        observation(exchange_ts=datetime(2026, 11, 20, 4, 0))
    with pytest.raises(RawObservationError, match="cannot precede"):
        observation(available_at=T0)
    with pytest.raises(RawObservationError, match="cannot follow"):
        observation(published_at=T0 + timedelta(seconds=1))
    with pytest.raises(RawObservationError, match="does not match"):
        observation(entitlement=permission("other-source"))


def test_raw_observation_deserialization_rejects_tampering_and_unknown_fields():
    row = observation().as_dict()
    row["payload_b64"] = "dGFtcGVyZWQ="
    with pytest.raises(RawObservationError, match="checksum does not match"):
        RawObservation.from_dict(row)
    with pytest.raises(RawObservationError, match="unknown fields"):
        RawObservation.from_dict({**observation().as_dict(), "extra": True})
