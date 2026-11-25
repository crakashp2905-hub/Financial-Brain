"""Coverage-aware exposure overlap across strategy and asset-class books.

Inputs must already be converted to signed INR notional against a shared canonical
exposure key (for example an issuer, index, sector or factor) and the same as-of time.
Options require delta-equivalent notional; ETF and mutual-fund constituents require
point-in-time look-through. Missing or unresolved positions are carried explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping


class ExposureOverlapError(ValueError):
    """Exposure book is malformed or cannot be compared on a common basis."""


def _decimal(value: object, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise ExposureOverlapError(f"{label} must be Decimal, integer or decimal text")
    try:
        result = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ExposureOverlapError(f"{label} must be a finite decimal") from exc
    if not result.is_finite():
        raise ExposureOverlapError(f"{label} must be a finite decimal")
    return result


def _aware_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ExposureOverlapError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ExposureBook:
    """Point-in-time exposure vector with unresolved gross risk kept visible."""

    book_id: str
    as_of: datetime
    exposures_inr: Mapping[str, Decimal | int | str]
    gross_exposures_inr: Mapping[str, Decimal | int | str] | None = None
    unresolved_gross_inr: Decimal | int | str = Decimal("0")
    source_refs: tuple[str, ...] = ()
    _normalized_exposures: dict[str, Decimal] = field(init=False, repr=False)
    _gross_by_key: dict[str, Decimal] = field(init=False, repr=False)
    _unresolved_gross: Decimal = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.book_id, str) or not self.book_id.strip():
            raise ExposureOverlapError("book_id is required")
        object.__setattr__(self, "as_of", _aware_utc(self.as_of, "as_of"))
        if not isinstance(self.exposures_inr, Mapping):
            raise ExposureOverlapError("exposures_inr must be a mapping")
        normalized: dict[str, Decimal] = {}
        for raw_key, raw_value in self.exposures_inr.items():
            if not isinstance(raw_key, str) or ":" not in raw_key:
                raise ExposureOverlapError("exposure keys must be canonical namespaced identifiers")
            key = raw_key.strip().upper()
            namespace, _, value = key.partition(":")
            if not namespace or not value or any(char.isspace() for char in key):
                raise ExposureOverlapError("exposure keys must be non-empty canonical identifiers")
            normalized[key] = _decimal(raw_value, f"exposure {key}")
        raw_gross = self.gross_exposures_inr
        gross_by_key: dict[str, Decimal] = {}
        if raw_gross is None:
            gross_by_key = {key: abs(value) for key, value in normalized.items()}
        else:
            if not isinstance(raw_gross, Mapping):
                raise ExposureOverlapError("gross_exposures_inr must be a mapping")
            for raw_key, raw_value in raw_gross.items():
                if not isinstance(raw_key, str) or ":" not in raw_key:
                    raise ExposureOverlapError(
                        "gross exposure keys must be canonical namespaced identifiers"
                    )
                key = raw_key.strip().upper()
                namespace, _, value = key.partition(":")
                if not namespace or not value or any(char.isspace() for char in key):
                    raise ExposureOverlapError("gross exposure keys must be non-empty identifiers")
                gross = _decimal(raw_value, f"gross exposure {key}")
                if gross < 0 or gross < abs(normalized.get(key, Decimal("0"))):
                    raise ExposureOverlapError(
                        "gross exposure must be non-negative and at least the absolute net exposure"
                    )
                gross_by_key[key] = gross
            if set(normalized) - set(gross_by_key):
                raise ExposureOverlapError("gross exposure map omits a net exposure key")
        unresolved = _decimal(self.unresolved_gross_inr, "unresolved_gross_inr")
        if unresolved < 0:
            raise ExposureOverlapError("unresolved_gross_inr cannot be negative")
        if not isinstance(self.source_refs, tuple) or any(
            not isinstance(ref, str) or not ref.strip() for ref in self.source_refs
        ):
            raise ExposureOverlapError("source_refs must be a tuple of non-empty references")
        object.__setattr__(self, "_normalized_exposures", normalized)
        object.__setattr__(self, "_gross_by_key", gross_by_key)
        object.__setattr__(self, "_unresolved_gross", unresolved)

    @property
    def known_gross_inr(self) -> Decimal:
        return sum(self._gross_by_key.values(), Decimal("0"))

    @property
    def total_gross_inr(self) -> Decimal:
        return self.known_gross_inr + self._unresolved_gross

    @property
    def coverage(self) -> Decimal:
        total = self.total_gross_inr
        return Decimal("1") if total == 0 else self.known_gross_inr / total


@dataclass(frozen=True)
class ExposureOverlap:
    """Pairwise shared exposure; the conservative ratio counts unknown gross in the base."""

    book_a: str
    book_b: str
    as_of: datetime
    known_common_gross_inr: Decimal
    signed_common_inr: Decimal
    min_total_gross_inr: Decimal
    min_known_gross_inr: Decimal
    conservative_overlap_ratio: Decimal | None
    known_overlap_ratio: Decimal | None
    directional_overlap_ratio: Decimal | None
    coverage_a: Decimal
    coverage_b: Decimal
    unresolved_gross_a_inr: Decimal
    unresolved_gross_b_inr: Decimal
    common_exposure_keys: tuple[str, ...]
    status: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True)
class ExposureComponent:
    """One sourced canonical exposure per ledger quantity unit, in signed INR."""

    key: str
    inr_per_unit: Decimal | int | str
    available_at: datetime
    source_ref: str

    def __post_init__(self) -> None:
        key = self.key.strip().upper() if isinstance(self.key, str) else ""
        namespace, _, value = key.partition(":")
        if not namespace or not value or any(char.isspace() for char in key):
            raise ExposureOverlapError("component key must be a canonical namespaced identifier")
        if not isinstance(self.source_ref, str) or not self.source_ref.strip():
            raise ExposureOverlapError("each exposure component needs a source reference")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "inr_per_unit", _decimal(self.inr_per_unit, "inr_per_unit"))
        object.__setattr__(self, "available_at", _aware_utc(self.available_at, "component available_at"))


@dataclass(frozen=True)
class InstrumentLookthrough:
    """Resolved constituents plus a sourced residual per unit for one ledger instrument."""

    components: tuple[ExposureComponent, ...]
    unresolved_gross_inr_per_unit: Decimal | int | str = Decimal("0")
    unresolved_source_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.components, tuple) or any(
            not isinstance(row, ExposureComponent) for row in self.components
        ):
            raise ExposureOverlapError("components must be a tuple of ExposureComponent rows")
        unresolved = _decimal(self.unresolved_gross_inr_per_unit,
                              "unresolved_gross_inr_per_unit")
        if unresolved < 0:
            raise ExposureOverlapError("unresolved per-unit gross cannot be negative")
        if unresolved and (not isinstance(self.unresolved_source_ref, str)
                          or not self.unresolved_source_ref.strip()):
            raise ExposureOverlapError("unresolved exposure needs a source reference")
        object.__setattr__(self, "unresolved_gross_inr_per_unit", unresolved)


@dataclass(frozen=True)
class UnresolvedPositionExposure:
    """Externally evidenced gross exposure for an unresolved legacy ledger position."""

    gross_inr: Decimal | int | str
    source_ref: str

    def __post_init__(self) -> None:
        gross = _decimal(self.gross_inr, "unresolved legacy gross_inr")
        if gross < 0 or not isinstance(self.source_ref, str) or not self.source_ref.strip():
            raise ExposureOverlapError("unresolved legacy exposure needs non-negative gross and source")
        object.__setattr__(self, "gross_inr", gross)


def exposure_book_from_canonical_snapshot(
    book_id: str,
    snapshot: Mapping[str, object],
    *,
    as_of: datetime,
    lookthrough: Mapping[tuple[str, str], InstrumentLookthrough],
    unresolved_legacy: Mapping[str, UnresolvedPositionExposure] | None = None,
    sleeve_ids: frozenset[str] | None = None,
) -> ExposureBook:
    """Translate a canonical portfolio ledger snapshot into a sourced exposure book.

    The look-through input must be produced from permission-cleared, point-in-time
    instrument/holdings/Greek data by a domain adapter. For options, `inr_per_unit` is the
    signed delta-equivalent exposure per ledger quantity unit; for ETFs/MFs, components
    describe sourced constituents and residual gross is explicit. No price or holding is
    inferred from ticker names. Unpriced positions or unresolved legacy rows fail closed.
    """
    if not isinstance(snapshot, Mapping) or not isinstance(lookthrough, Mapping):
        raise ExposureOverlapError("snapshot and lookthrough must be mappings")
    as_of_utc = _aware_utc(as_of, "as_of")
    raw_as_of = snapshot.get("as_of")
    if not isinstance(raw_as_of, str):
        raise ExposureOverlapError("canonical snapshot must be requested with an explicit as-of time")
    try:
        snapshot_as_of = datetime.fromisoformat(raw_as_of.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExposureOverlapError("canonical snapshot as-of time is malformed") from exc
    if _aware_utc(snapshot_as_of, "snapshot as_of") != as_of_utc:
        raise ExposureOverlapError("snapshot and exposure as-of times differ")

    positions = snapshot.get("positions")
    unresolved_rows = snapshot.get("unresolved_positions")
    if not isinstance(positions, list) or not isinstance(unresolved_rows, list):
        raise ExposureOverlapError("canonical snapshot must contain position and unresolved-position lists")
    legacy_map = unresolved_legacy or {}
    selected_unresolved = [row for row in unresolved_rows if isinstance(row, dict) and (
        sleeve_ids is None or str(row.get("sleeve_id")) in sleeve_ids
    )]
    expected_legacy_ids = {str(row.get("source_event_id")) for row in selected_unresolved}
    if set(legacy_map) != expected_legacy_ids:
        raise ExposureOverlapError("every unresolved legacy position needs exactly one sourced exposure")

    net_by_key: dict[str, Decimal] = {}
    gross_by_key: dict[str, Decimal] = {}
    unresolved_gross = Decimal("0")
    refs: set[str] = set()
    expected_instruments: set[tuple[str, str]] = set()
    for row in positions:
        if not isinstance(row, dict):
            raise ExposureOverlapError("snapshot position rows must be mappings")
        sleeve = str(row.get("sleeve_id", ""))
        if sleeve_ids is not None and sleeve not in sleeve_ids:
            continue
        instrument = str(row.get("instrument_id", "")).strip().upper()
        asset_class = str(row.get("asset_class", "")).strip().upper()
        quantity = _decimal(row.get("quantity"), "position quantity")
        if quantity == 0:
            continue
        if not instrument or not asset_class:
            raise ExposureOverlapError("non-zero positions need asset class and instrument identity")
        if str(row.get("currency", "")).upper() != "INR":
            raise ExposureOverlapError("canonical exposure adapter requires INR positions")
        mark = _decimal(row.get("mark"), f"mark for {instrument}")
        multiplier = _decimal(row.get("multiplier"), f"multiplier for {instrument}")
        if mark <= 0 or multiplier <= 0:
            raise ExposureOverlapError("non-zero positions need positive mark and multiplier")
        mark_at = row.get("mark_at")
        if not isinstance(mark_at, str):
            raise ExposureOverlapError(f"position {instrument} lacks a sourced mark timestamp")
        try:
            mark_time = datetime.fromisoformat(mark_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ExposureOverlapError(f"position {instrument} mark timestamp is malformed") from exc
        if _aware_utc(mark_time, f"mark timestamp for {instrument}") > as_of_utc:
            raise ExposureOverlapError(f"position {instrument} mark is from after the as-of time")
        lookup_key = (asset_class, instrument)
        expected_instruments.add(lookup_key)
        entry = lookthrough.get(lookup_key)
        if not isinstance(entry, InstrumentLookthrough):
            raise ExposureOverlapError(f"position {instrument} lacks a domain look-through record")
        for component in entry.components:
            if component.available_at > as_of_utc:
                raise ExposureOverlapError(f"component {component.key} was unavailable at the as-of time")
            signed = quantity * Decimal(component.inr_per_unit)
            net_by_key[component.key] = net_by_key.get(component.key, Decimal("0")) + signed
            gross_by_key[component.key] = gross_by_key.get(component.key, Decimal("0")) + abs(signed)
            refs.add(component.source_ref)
        unresolved_gross += abs(quantity) * Decimal(entry.unresolved_gross_inr_per_unit)
        if entry.unresolved_source_ref:
            refs.add(entry.unresolved_source_ref)

    if set(lookthrough) != expected_instruments:
        raise ExposureOverlapError("look-through map has stale or out-of-scope instrument entries")
    for evidence in legacy_map.values():
        if not isinstance(evidence, UnresolvedPositionExposure):
            raise ExposureOverlapError("legacy exposure values must be UnresolvedPositionExposure")
        unresolved_gross += Decimal(evidence.gross_inr)
        refs.add(evidence.source_ref)
    used_refs = tuple(sorted(refs))
    if not used_refs:
        raise ExposureOverlapError("exposure snapshot requires source references")
    return ExposureBook(
        book_id=book_id,
        as_of=as_of_utc,
        exposures_inr=net_by_key,
        gross_exposures_inr=gross_by_key,
        unresolved_gross_inr=unresolved_gross,
        source_refs=used_refs,
    )


def compare_exposure_books(a: ExposureBook, b: ExposureBook) -> ExposureOverlap:
    """Compare two books without hiding unknown look-through or mismatched timestamps.

    The conservative overlap denominator is the smaller *total* gross book exposure,
    including unresolved holdings, making it a lower bound when look-through is partial.
    The known-only ratio is shown separately and must not be mistaken for full-book overlap.
    """
    if not isinstance(a, ExposureBook) or not isinstance(b, ExposureBook):
        raise ExposureOverlapError("both inputs must be ExposureBook instances")
    if a.as_of != b.as_of:
        raise ExposureOverlapError("books must share an identical as-of timestamp")
    keys = tuple(sorted(set(a._gross_by_key) & set(b._gross_by_key)))
    common_gross = Decimal("0")
    signed_common = Decimal("0")
    for key in keys:
        left, right = a._normalized_exposures.get(key, Decimal("0")), b._normalized_exposures.get(key, Decimal("0"))
        shared = min(a._gross_by_key[key], b._gross_by_key[key])
        common_gross += shared
        if left and right:
            signed_common += min(abs(left), abs(right)) if (left > 0) == (right > 0) \
                else -min(abs(left), abs(right))

    min_total = min(a.total_gross_inr, b.total_gross_inr)
    min_known = min(a.known_gross_inr, b.known_gross_inr)
    status = "NO_EXPOSURE" if min_total == 0 else (
        "INCOMPLETE_LOOKTHROUGH" if a._unresolved_gross or b._unresolved_gross
        else "MEASURABLE"
    )
    return ExposureOverlap(
        book_a=a.book_id,
        book_b=b.book_id,
        as_of=a.as_of,
        known_common_gross_inr=common_gross,
        signed_common_inr=signed_common,
        min_total_gross_inr=min_total,
        min_known_gross_inr=min_known,
        conservative_overlap_ratio=(common_gross / min_total if min_total else None),
        known_overlap_ratio=(common_gross / min_known if min_known else None),
        directional_overlap_ratio=(signed_common / min_total if min_total else None),
        coverage_a=a.coverage,
        coverage_b=b.coverage,
        unresolved_gross_a_inr=a._unresolved_gross,
        unresolved_gross_b_inr=b._unresolved_gross,
        common_exposure_keys=keys,
        status=status,
        source_refs=tuple(sorted(set(a.source_refs + b.source_refs))),
    )


__all__ = [
    "ExposureBook", "ExposureComponent", "ExposureOverlap", "ExposureOverlapError",
    "InstrumentLookthrough", "UnresolvedPositionExposure", "compare_exposure_books",
    "exposure_book_from_canonical_snapshot",
]
