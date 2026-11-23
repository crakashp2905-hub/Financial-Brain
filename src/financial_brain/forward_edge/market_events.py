"""Normalized, timestamp-explicit market event envelope (FE-0033).

Events preserve source clocks and identity. The type does not imply that a value
was independently observed; provenance belongs in ``source`` and ``evidence_id``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class MarketEventError(ValueError):
    """Market event is malformed or violates its clock/identity contract."""


class EventKind(StrEnum):
    TICK = "tick"
    QUOTE = "quote"
    BOOK_DEPTH = "book_depth"
    BAR = "bar"
    HALT = "halt"
    AUCTION = "auction"
    CORPORATE_ACTION = "corporate_action"
    BROKER_UPDATE = "broker_update"


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MarketEventError(f"{field} must be timezone aware")
    return value.astimezone(timezone.utc)


def _finite_number(value: Any, field: str, *, allow_zero: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MarketEventError(f"{field} must be numeric")
    number = float(value)
    if not number == number or number in (float("inf"), float("-inf")) or (not allow_zero and number <= 0):
        raise MarketEventError(f"{field} must be finite" + (" and positive" if not allow_zero else ""))
    return number


@dataclass(frozen=True)
class MarketEvent:
    event_id: str
    kind: EventKind
    venue: str
    instrument_id: str
    event_time: datetime
    received_at: datetime
    available_at: datetime
    payload: dict[str, Any]
    source: str
    sequence: int | None = None
    decision_time: datetime | None = None
    evidence_id: str | None = None
    source_at: datetime | None = None
    exchange_at: datetime | None = None
    published_at: datetime | None = None
    revision_of: str | None = None

    def __post_init__(self) -> None:
        if not all(isinstance(x, str) and x.strip() for x in
                   (self.event_id, self.venue, self.instrument_id, self.source)):
            raise MarketEventError("event id, venue, instrument and source are required")
        try:
            kind = EventKind(self.kind)
        except ValueError as exc:
            raise MarketEventError(f"unsupported event kind {self.kind!r}") from exc
        object.__setattr__(self, "kind", kind)
        for name in ("event_time", "received_at", "available_at"):
            object.__setattr__(self, name, _utc(getattr(self, name), name))
        for name in ("decision_time", "source_at", "exchange_at", "published_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _utc(value, name))
        if self.published_at is not None and self.published_at > self.available_at:
            raise MarketEventError("event cannot be available to strategy before publication")
        if self.revision_of is not None and (
            not isinstance(self.revision_of, str)
            or not self.revision_of.strip()
            or self.revision_of == self.event_id
        ):
            raise MarketEventError("revision_of must identify a different non-empty event ID")
        if self.sequence is not None and (type(self.sequence) is not int or self.sequence < 0):
            raise MarketEventError("sequence must be a non-negative integer")
        if not isinstance(self.payload, dict):
            raise MarketEventError("payload must be an object")
        self._validate_payload()
        if self.decision_time is not None and self.available_at > self.decision_time:
            raise MarketEventError("event was not available at the stated decision_time")

    def _validate_payload(self) -> None:
        p = self.payload
        if self.kind == EventKind.TICK:
            _finite_number(p.get("price"), "price", allow_zero=False)
            if "quantity" in p and _finite_number(p["quantity"], "quantity") < 0:
                raise MarketEventError("quantity must be non-negative")
        elif self.kind == EventKind.QUOTE:
            bid = _finite_number(p.get("bid"), "bid", allow_zero=False)
            ask = _finite_number(p.get("ask"), "ask", allow_zero=False)
            if bid > ask:
                raise MarketEventError("bid cannot exceed ask")
            for key in ("bid_quantity", "ask_quantity"):
                if key in p and _finite_number(p[key], key) < 0:
                    raise MarketEventError(f"{key} must be non-negative")
        elif self.kind == EventKind.BOOK_DEPTH:
            for side in ("bids", "asks"):
                levels = p.get(side)
                if not isinstance(levels, list):
                    raise MarketEventError(f"{side} must be a list of price/quantity levels")
                prior = None
                for level in levels:
                    if not isinstance(level, (list, tuple)) or len(level) != 2:
                        raise MarketEventError("depth level must be (price, quantity)")
                    price = _finite_number(level[0], "depth price", allow_zero=False)
                    quantity = _finite_number(level[1], "depth quantity")
                    if quantity < 0 or (prior is not None and ((side == "bids" and price > prior) or
                                                               (side == "asks" and price < prior))):
                        raise MarketEventError("depth levels must be non-negative and price sorted")
                    prior = price
        elif self.kind == EventKind.BAR:
            values = {key: _finite_number(p.get(key), key, allow_zero=key == "volume")
                      for key in ("open", "high", "low", "close", "volume")}
            if values["volume"] < 0 or values["high"] < max(values["open"], values["close"], values["low"]) or \
                    values["low"] > min(values["open"], values["close"], values["high"]):
                raise MarketEventError("bar OHLCV is inconsistent")
        elif self.kind in (EventKind.HALT, EventKind.AUCTION, EventKind.CORPORATE_ACTION, EventKind.BROKER_UPDATE):
            if not p:
                raise MarketEventError(f"{self.kind.value} payload cannot be empty")

    def as_dict(self) -> dict[str, Any]:
        result = {"event_id": self.event_id, "kind": self.kind.value, "venue": self.venue,
                "instrument_id": self.instrument_id, "event_time": self.event_time.isoformat(),
                "received_at": self.received_at.isoformat(), "available_at": self.available_at.isoformat(),
                "decision_time": self.decision_time.isoformat() if self.decision_time else None,
                "sequence": self.sequence, "source": self.source, "evidence_id": self.evidence_id,
                "payload": self.payload}
        # Preserve legacy event hashes unless a source explicitly supplies these separate clocks.
        for name in ("source_at", "exchange_at", "published_at"):
            value = getattr(self, name)
            if value is not None:
                result[name] = value.isoformat()
        if self.revision_of is not None:
            result["revision_of"] = self.revision_of
        return result

    def sha256(self) -> str:
        raw = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def dedupe_sha256(self) -> str:
        """Hash source identity and payload, excluding local receipt/availability clocks."""
        payload = json.dumps(self.payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        source_clock = self.source_at or self.exchange_at or self.event_time
        body = {
            "event_id": self.event_id,
            "kind": self.kind.value,
            "venue": self.venue,
            "instrument_id": self.instrument_id,
            "source": self.source,
            "sequence": self.sequence,
            "source_clock": source_clock.isoformat(),
            "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            "revision_of": self.revision_of,
        }
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MarketEvent":
        fields = dict(data)
        for key in ("event_time", "received_at", "available_at", "decision_time",
                    "source_at", "exchange_at", "published_at"):
            if fields.get(key) is not None:
                fields[key] = datetime.fromisoformat(fields[key])
        return cls(**fields)
