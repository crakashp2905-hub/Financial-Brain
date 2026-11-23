"""Immutable, checksummed source observation with explicit availability and rights metadata."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .data_permissions import DataPermission


class RawObservationError(ValueError):
    """A raw observation lacks trustworthy source, clock, checksum or rights metadata."""


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RawObservationError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class RawObservation:
    """A source payload before normalization, bound to clocks, revision and permission state.

    The payload is stored as immutable bytes so source formatting can be retained. The
    checksum covers those exact bytes; it does not attest the claimed provider identity.
    Unknown/denied rights are preserved as data and are not upgraded by this container.
    """

    source_id: str
    instrument_id: str
    exchange_ts: datetime
    received_ts: datetime
    available_at: datetime
    published_at: datetime | None
    revision_id: str
    raw_payload: bytes
    entitlement: DataPermission
    checksum: str | None = None

    def __post_init__(self) -> None:
        for field in ("source_id", "instrument_id", "revision_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise RawObservationError(f"{field} is required")
            object.__setattr__(self, field, value.strip())
        for field in ("exchange_ts", "received_ts", "available_at"):
            object.__setattr__(self, field, _utc(getattr(self, field), field))
        if self.published_at is not None:
            object.__setattr__(self, "published_at", _utc(self.published_at, "published_at"))
        if self.received_ts > self.available_at:
            raise RawObservationError("observation cannot be available before it was received")
        if self.published_at is not None and self.published_at > self.available_at:
            raise RawObservationError("observation cannot be available before publication")
        if not isinstance(self.raw_payload, bytes) or not self.raw_payload:
            raise RawObservationError("raw_payload must be non-empty immutable bytes")
        if not isinstance(self.entitlement, DataPermission):
            raise RawObservationError("entitlement must be a DataPermission record")
        if self.entitlement.source_id != self.source_id:
            raise RawObservationError("entitlement source_id must match the observation source")
        calculated = hashlib.sha256(self.raw_payload).hexdigest()
        if self.checksum is not None:
            if not isinstance(self.checksum, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", self.checksum):
                raise RawObservationError("checksum must be a SHA-256 hex digest")
            if self.checksum.lower() != calculated:
                raise RawObservationError("checksum does not match the exact raw payload bytes")
        object.__setattr__(self, "checksum", calculated)

    @property
    def payload(self) -> object:
        """Decode JSON payload without exposing mutable state stored on this instance."""
        try:
            return json.loads(self.raw_payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RawObservationError("raw payload is not valid UTF-8 JSON") from exc

    def as_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "instrument_id": self.instrument_id,
            "exchange_ts": self.exchange_ts.isoformat(),
            "received_ts": self.received_ts.isoformat(),
            "available_at": self.available_at.isoformat(),
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "revision_id": self.revision_id,
            "checksum": self.checksum,
            "raw_payload_hex": self.raw_payload.hex(),
            "entitlement": {
                "source_id": self.entitlement.source_id,
                "use": self.entitlement.use.value,
                "status": self.entitlement.status.value,
                "effective_from": (self.entitlement.effective_from.isoformat()
                                   if self.entitlement.effective_from else None),
                "effective_through": (self.entitlement.effective_through.isoformat()
                                      if self.entitlement.effective_through else None),
                "evidence_ref": self.entitlement.evidence_ref,
                "reviewer": self.entitlement.reviewer,
            },
        }


__all__ = ["RawObservation", "RawObservationError"]
