"""Source-aware, effective-dated security and derivative contract identities.

The registry stores provenance and contract terms; it does not manufacture a
listed option contract or imply that a provider's instrument file may be retained.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any, Iterable


class InstrumentMasterError(ValueError):
    """Instrument identity or effective-dated contract data is invalid."""


class NoOptionTrade(InstrumentMasterError):
    """No exact, source-backed listed option contract exists for the request."""


KINDS = {"EQUITY", "ETF", "INDEX", "FUTURE", "OPTION"}
RIGHTS = {"CE", "PE"}
HASH = re.compile(r"^[0-9a-f]{64}$")


def _parse_date(value: Any, name: str, *, required: bool = True) -> date | None:
    if value is None and not required:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InstrumentMasterError(f"{name} must be an ISO date") from exc


def _required_date(value: Any, name: str) -> date:
    parsed = _parse_date(value, name)
    if parsed is None:
        raise InstrumentMasterError(f"{name} is required")
    return parsed


def _parse_timestamp(value: Any, name: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise InstrumentMasterError(f"{name} must be an ISO timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise InstrumentMasterError(f"{name} must be timezone-aware")
    return result


@dataclass(frozen=True)
class InstrumentRecord:
    canonical_id: str
    kind: str
    source_id: str
    source_token: str
    exchange: str
    segment: str
    trading_symbol: str
    effective_from: date
    effective_through: date | None
    source_published_at: datetime
    received_at: datetime
    source_payload_sha256: str
    lot_size: int
    tick_size: Decimal
    underlying_id: str | None = None
    isin: str | None = None
    expiry: date | None = None
    strike: Decimal | None = None
    right: str | None = None
    adjustment_factor: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        for name in ("canonical_id", "kind", "source_id", "source_token", "exchange", "segment",
                     "trading_symbol"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise InstrumentMasterError(f"{name} is required")
        if self.kind not in KINDS:
            raise InstrumentMasterError(f"unsupported instrument kind: {self.kind}")
        if self.effective_through is not None and self.effective_through < self.effective_from:
            raise InstrumentMasterError("instrument effective-through date precedes effective-from date")
        if self.lot_size < 1 or not self.tick_size.is_finite() or self.tick_size <= 0:
            raise InstrumentMasterError("instrument lot size and tick size must be positive")
        if not HASH.fullmatch(self.source_payload_sha256):
            raise InstrumentMasterError("source payload SHA-256 must be 64 lowercase hexadecimal characters")
        if self.source_published_at.tzinfo is None or self.received_at.tzinfo is None:
            raise InstrumentMasterError("instrument source and receipt timestamps must be timezone-aware")
        if self.source_published_at > self.received_at:
            raise InstrumentMasterError("instrument source publication cannot follow local receipt")
        if not self.adjustment_factor.is_finite() or self.adjustment_factor <= 0:
            raise InstrumentMasterError("instrument adjustment factor must be finite and positive")
        if self.kind in {"EQUITY", "ETF"} and not self.isin:
            raise InstrumentMasterError(f"{self.kind} instrument requires an ISIN")
        if self.kind in {"FUTURE", "OPTION"} and (not self.underlying_id or self.expiry is None):
            raise InstrumentMasterError(f"{self.kind} contract requires an underlying and expiry")
        if self.kind in {"FUTURE", "OPTION"} and self.expiry is not None and self.expiry < self.effective_from:
            raise InstrumentMasterError("contract expiry precedes its effective-from date")
        if self.kind == "OPTION":
            if self.strike is None or not self.strike.is_finite() or self.strike <= 0:
                raise InstrumentMasterError("option contract requires a finite positive listed strike")
            if self.right not in RIGHTS:
                raise InstrumentMasterError("option right must be CE or PE")
        elif self.strike is not None or self.right is not None:
            raise InstrumentMasterError("strike and option right are valid only for listed options")

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "InstrumentRecord":
        required = {
            "canonical_id", "kind", "source_id", "source_token", "exchange", "segment",
            "trading_symbol", "effective_from", "effective_through", "source_published_at",
            "received_at", "source_payload_sha256", "lot_size", "tick_size", "underlying_id",
            "isin", "expiry", "strike", "right", "adjustment_factor",
        }
        if set(row) != required:
            raise InstrumentMasterError("instrument record fields do not match the v1 contract")
        try:
            lot_size = row["lot_size"]
            if type(lot_size) is not int:
                raise TypeError("lot_size must be an integer")
            return cls(
                canonical_id=str(row["canonical_id"]), kind=str(row["kind"]).upper(),
                source_id=str(row["source_id"]), source_token=str(row["source_token"]),
                exchange=str(row["exchange"]).upper(), segment=str(row["segment"]).upper(),
                trading_symbol=str(row["trading_symbol"]),
                effective_from=_required_date(row["effective_from"], "effective_from"),
                effective_through=_parse_date(row["effective_through"], "effective_through", required=False),
                source_published_at=_parse_timestamp(row["source_published_at"], "source_published_at"),
                received_at=_parse_timestamp(row["received_at"], "received_at"),
                source_payload_sha256=str(row["source_payload_sha256"]), lot_size=lot_size,
                tick_size=Decimal(str(row["tick_size"])),
                underlying_id=str(row["underlying_id"]) if row["underlying_id"] else None,
                isin=str(row["isin"]).upper() if row["isin"] else None,
                expiry=_parse_date(row["expiry"], "expiry", required=False),
                strike=Decimal(str(row["strike"])) if row["strike"] is not None else None,
                right=str(row["right"]).upper() if row["right"] else None,
                adjustment_factor=Decimal(str(row["adjustment_factor"])),
            )
        except (InvalidOperation, TypeError, ValueError) as exc:
            if isinstance(exc, InstrumentMasterError):
                raise
            raise InstrumentMasterError("instrument record has malformed values") from exc

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        for name in ("effective_from", "effective_through", "expiry"):
            row[name] = row[name].isoformat() if row[name] else None
        for name in ("source_published_at", "received_at"):
            row[name] = row[name].isoformat()
        for name in ("tick_size", "strike", "adjustment_factor"):
            row[name] = str(row[name]) if row[name] is not None else None
        return row


@dataclass(frozen=True)
class InstrumentAdjustment:
    canonical_id: str
    action_id: str
    action_type: str
    effective_on: date
    price_factor: Decimal
    volume_factor: Decimal
    source_id: str
    source_published_at: datetime
    received_at: datetime
    source_payload_sha256: str

    def __post_init__(self) -> None:
        if not all(isinstance(v, str) and v.strip() for v in (
                self.canonical_id, self.action_id, self.action_type, self.source_id)):
            raise InstrumentMasterError("adjustments require instrument, action, type and source identifiers")
        if (not self.price_factor.is_finite() or self.price_factor <= 0
                or not self.volume_factor.is_finite() or self.volume_factor <= 0):
            raise InstrumentMasterError("adjustment factors must be finite and positive")
        if self.source_published_at.tzinfo is None or self.received_at.tzinfo is None:
            raise InstrumentMasterError("adjustment timestamps must be timezone-aware")
        if self.source_published_at > self.received_at or not HASH.fullmatch(self.source_payload_sha256):
            raise InstrumentMasterError("adjustment source timing or payload hash is invalid")


class InstrumentMaster:
    """Immutable source-aware dated instrument and adjustment registry."""

    def __init__(self, records: Iterable[InstrumentRecord],
                 adjustments: Iterable[InstrumentAdjustment] = ()) -> None:
        self.records = tuple(sorted(records, key=lambda r: (r.canonical_id, r.effective_from, r.source_id,
                                                            r.source_token)))
        self.adjustments = tuple(sorted(adjustments, key=lambda a: (a.canonical_id, a.effective_on,
                                                                    a.source_id, a.action_id)))
        if not self.records:
            raise InstrumentMasterError("instrument master contains no records")
        known_ids = {row.canonical_id for row in self.records}
        if any(row.canonical_id not in known_ids for row in self.adjustments):
            raise InstrumentMasterError("corporate adjustment references an unknown canonical instrument")
        self._validate_ranges()
        self.digest = hashlib.sha256(json.dumps({
            "records": [row.as_dict() for row in self.records],
            "adjustments": [self._adjustment_dict(row) for row in self.adjustments],
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> "InstrumentMaster":
        if not isinstance(document, dict) or document.get("schema") != "fb.instrument_master.v1":
            raise InstrumentMasterError("instrument master has an unsupported schema")
        if set(document) != {"schema", "records", "adjustments", "sha256"}:
            raise InstrumentMasterError("instrument master document fields do not match the v1 contract")
        rows = document.get("records")
        adjustment_rows = document.get("adjustments", [])
        if not isinstance(rows, list) or not isinstance(adjustment_rows, list):
            raise InstrumentMasterError("instrument master records and adjustments must be arrays")
        records = [InstrumentRecord.from_dict(row) for row in rows if isinstance(row, dict)]
        if len(records) != len(rows):
            raise InstrumentMasterError("instrument master contains a non-object record")
        adjustments: list[InstrumentAdjustment] = []
        for row in adjustment_rows:
            try:
                expected = {"canonical_id", "action_id", "action_type", "effective_on", "price_factor",
                            "volume_factor", "source_id", "source_published_at", "received_at",
                            "source_payload_sha256"}
                if not isinstance(row, dict) or set(row) != expected:
                    raise InstrumentMasterError("adjustment fields do not match the v1 contract")
                adjustments.append(InstrumentAdjustment(
                    canonical_id=str(row["canonical_id"]), action_id=str(row["action_id"]),
                    action_type=str(row["action_type"]),
                    effective_on=_required_date(row["effective_on"], "effective_on"),
                    price_factor=Decimal(str(row["price_factor"])),
                    volume_factor=Decimal(str(row["volume_factor"])), source_id=str(row["source_id"]),
                    source_published_at=_parse_timestamp(row["source_published_at"], "source_published_at"),
                    received_at=_parse_timestamp(row["received_at"], "received_at"),
                    source_payload_sha256=str(row["source_payload_sha256"]),
                ))
            except (KeyError, TypeError, InvalidOperation, ValueError) as exc:
                if isinstance(exc, InstrumentMasterError):
                    raise
                raise InstrumentMasterError("instrument master contains a malformed adjustment") from exc
        master = cls(records, adjustments)
        if document.get("sha256") != master.digest:
            raise InstrumentMasterError("instrument master content hash does not match its records")
        return master

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "fb.instrument_master.v1",
            "records": [row.as_dict() for row in self.records],
            "adjustments": [self._adjustment_dict(row) for row in self.adjustments],
            "sha256": self.digest,
        }

    @staticmethod
    def _adjustment_dict(row: InstrumentAdjustment) -> dict[str, Any]:
        return {
            "canonical_id": row.canonical_id, "action_id": row.action_id,
            "action_type": row.action_type, "effective_on": row.effective_on.isoformat(),
            "price_factor": str(row.price_factor), "volume_factor": str(row.volume_factor),
            "source_id": row.source_id, "source_published_at": row.source_published_at.isoformat(),
            "received_at": row.received_at.isoformat(), "source_payload_sha256": row.source_payload_sha256,
        }

    def _validate_ranges(self) -> None:
        groups: dict[tuple[str, str, str], list[InstrumentRecord]] = {}
        for record in self.records:
            groups.setdefault((record.source_id, record.exchange, record.source_token), []).append(record)
        for key, rows in groups.items():
            ordered = sorted(rows, key=lambda r: r.effective_from)
            for prior, current in zip(ordered, ordered[1:]):
                if prior.effective_through is None or prior.effective_through >= current.effective_from:
                    raise InstrumentMasterError(f"source token has overlapping effective identity ranges: {key}")

    def resolve_token(self, *, source_id: str, exchange: str, source_token: str,
                      as_of: date) -> InstrumentRecord:
        matches = [row for row in self.records if row.source_id == source_id and row.exchange == exchange.upper()
                   and row.source_token == str(source_token) and row.effective_from <= as_of
                   and (row.effective_through is None or as_of <= row.effective_through)]
        if len(matches) != 1:
            raise InstrumentMasterError("source token does not resolve to one effective instrument record")
        return matches[0]

    def resolve_option(self, *, source_id: str, underlying_id: str, expiry: date, strike: Decimal,
                       right: str, as_of: date) -> InstrumentRecord:
        if right.upper() not in RIGHTS or strike <= 0:
            raise NoOptionTrade("NO_OPTION_TRADE: invalid exact option contract request")
        if as_of > expiry:
            raise NoOptionTrade("NO_OPTION_TRADE: listed option contract has expired")
        matches = [row for row in self.records if row.kind == "OPTION" and row.source_id == source_id
                   and row.underlying_id == underlying_id and row.expiry == expiry and row.strike == strike
                   and row.right == right.upper() and row.effective_from <= as_of
                   and (row.effective_through is None or as_of <= row.effective_through)]
        if len(matches) != 1:
            raise NoOptionTrade("NO_OPTION_TRADE: exact listed source-backed option contract is unavailable")
        return matches[0]

    def reconcile_optionable_underlyings(self, *, source_id: str, candidates: Iterable[str],
                                         as_of: date) -> list[dict[str, Any]]:
        """Mark index candidates only when an effective, unexpired listed option exists."""
        eligible = [row for row in self.records if row.kind == "OPTION" and row.source_id == source_id
                    and row.underlying_id and row.expiry is not None and row.expiry >= as_of
                    and row.effective_from <= as_of
                    and (row.effective_through is None or as_of <= row.effective_through)]
        counts: dict[str, int] = {}
        for row in eligible:
            counts[row.underlying_id] = counts.get(row.underlying_id, 0) + 1
        normalized = sorted({str(candidate).strip() for candidate in candidates if str(candidate).strip()})
        return [{
            "underlying_id": candidate,
            "status": "OPTIONABLE" if counts.get(candidate, 0) else "NO_OPTION_TRADE",
            "listed_contract_count": counts.get(candidate, 0),
            "as_of": as_of.isoformat(),
            "source_id": source_id,
            "instrument_master_sha256": self.digest,
        } for candidate in normalized]

    def adjustment_factors(self, *, canonical_id: str, as_of: date) -> tuple[Decimal, Decimal]:
        applicable = [row for row in self.adjustments if row.canonical_id == canonical_id
                      and row.effective_on <= as_of]
        price = Decimal("1")
        volume = Decimal("1")
        for row in applicable:
            price *= row.price_factor
            volume *= row.volume_factor
        return price, volume
