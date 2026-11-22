"""Immutable, permission-bound raw market observation contract (SL-01-01)."""
from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from financial_brain.forward_edge.data_permissions import DataPermission


class RawObservationError(ValueError):
    """A raw observation is malformed or its provenance does not reconcile."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _utc(value: datetime | None, name: str, *, required: bool = True) -> datetime | None:
    if value is None and not required:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RawObservationError(f"{name} must be timezone aware")
    return value.astimezone(timezone.utc)


def _parse_datetime(value: object, name: str, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise RawObservationError(f"{name} must be an ISO-8601 string")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise RawObservationError(f"{name} is not a valid ISO-8601 timestamp") from exc


def _string_field(row: dict[str, object], key: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


@dataclass(frozen=True)
class RawObservation:
    """A source payload and the exact clocks/rights under which it was observed.

    `payload` is immutable bytes so the verified checksum always refers to the
    stored observation. Rights status is recorded here; consumers still have to
    authorize their specific use through the permission gate.
    """

    source_id: str
    instrument_id: str
    exchange_ts: datetime
    received_ts: datetime
    available_at: datetime
    published_at: datetime | None
    revision_id: str
    checksum: str
    entitlement: DataPermission
    payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        for name in ("source_id", "instrument_id", "revision_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise RawObservationError(f"{name} is required")
            object.__setattr__(self, name, value.strip())
        for name in ("exchange_ts", "received_ts", "available_at", "published_at"):
            object.__setattr__(self, name, _utc(getattr(self, name), name, required=name != "published_at"))
        if self.available_at < self.received_ts:
            raise RawObservationError("available_at cannot precede received_ts")
        if self.published_at is not None and self.published_at > self.available_at:
            raise RawObservationError("published_at cannot follow available_at")
        if not isinstance(self.payload, bytes):
            raise RawObservationError("payload must be immutable bytes")
        if not isinstance(self.checksum, str) or not _SHA256.fullmatch(self.checksum):
            raise RawObservationError("checksum must be a lowercase SHA-256 hex digest")
        if hashlib.sha256(self.payload).hexdigest() != self.checksum:
            raise RawObservationError("checksum does not match raw payload")
        if not isinstance(self.entitlement, DataPermission):
            raise RawObservationError("entitlement must be a DataPermission")
        if self.entitlement.source_id != self.source_id:
            raise RawObservationError("entitlement source does not match observation source")

    @classmethod
    def capture(cls, *, source_id: str, instrument_id: str, exchange_ts: datetime,
                received_ts: datetime, available_at: datetime, published_at: datetime | None,
                revision_id: str, payload: bytes, entitlement: DataPermission) -> "RawObservation":
        """Construct an observation while calculating the payload digest."""
        if not isinstance(payload, bytes):
            raise RawObservationError("payload must be immutable bytes")
        return cls(source_id, instrument_id, exchange_ts, received_ts, available_at,
                   published_at, revision_id, hashlib.sha256(payload).hexdigest(),
                   entitlement, payload)

    def as_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-safe representation including payload bytes."""
        permission = self.entitlement
        return {
            "source_id": self.source_id,
            "instrument_id": self.instrument_id,
            "exchange_ts": self.exchange_ts.isoformat(),
            "received_ts": self.received_ts.isoformat(),
            "available_at": self.available_at.isoformat(),
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "revision_id": self.revision_id,
            "checksum": self.checksum,
            "entitlement": {
                "source_id": permission.source_id,
                "use": permission.use.value,
                "status": permission.status.value,
                "effective_from": permission.effective_from.isoformat() if permission.effective_from else None,
                "effective_through": permission.effective_through.isoformat() if permission.effective_through else None,
                "evidence_ref": permission.evidence_ref,
                "reviewer": permission.reviewer,
            },
            "payload_b64": base64.b64encode(self.payload).decode("ascii"),
        }

    @classmethod
    def from_dict(cls, row: dict[str, object]) -> "RawObservation":
        """Rehydrate only an exact schema and revalidate every clock and digest."""
        expected = {"source_id", "instrument_id", "exchange_ts", "received_ts", "available_at",
                    "published_at", "revision_id", "checksum", "entitlement", "payload_b64"}
        if set(row) != expected:
            raise RawObservationError("raw observation has missing or unknown fields")
        try:
            permission_row = row["entitlement"]
            if not isinstance(permission_row, dict):
                raise ValueError("entitlement must be an object")
            permission = DataPermission.from_dict(permission_row)
            encoded_payload = row["payload_b64"]
            if not isinstance(encoded_payload, str):
                raise ValueError("payload_b64 must be a string")
            source_id = _string_field(row, "source_id")
            instrument_id = _string_field(row, "instrument_id")
            revision_id = _string_field(row, "revision_id")
            checksum = _string_field(row, "checksum")
            payload = base64.b64decode(encoded_payload, validate=True)
            exchange_ts = _parse_datetime(row["exchange_ts"], "exchange_ts")
            received_ts = _parse_datetime(row["received_ts"], "received_ts")
            available_at = _parse_datetime(row["available_at"], "available_at")
            published_at = _parse_datetime(row["published_at"], "published_at", optional=True)
            if exchange_ts is None or received_ts is None or available_at is None:
                raise ValueError("required timestamp is missing")
            return cls(
                source_id=source_id, instrument_id=instrument_id,
                exchange_ts=exchange_ts, received_ts=received_ts, available_at=available_at,
                published_at=published_at,
                revision_id=revision_id, checksum=checksum,
                entitlement=permission, payload=payload,
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, RawObservationError):
                raise
            raise RawObservationError(f"invalid raw observation: {exc}") from exc
