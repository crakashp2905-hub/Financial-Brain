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


def _decimal(value: Decimal | int | str, label: str) -> Decimal:
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
    unresolved_gross_inr: Decimal | int | str = Decimal("0")
    source_refs: tuple[str, ...] = ()
    _normalized_exposures: dict[str, Decimal] = field(init=False, repr=False)
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
        unresolved = _decimal(self.unresolved_gross_inr, "unresolved_gross_inr")
        if unresolved < 0:
            raise ExposureOverlapError("unresolved_gross_inr cannot be negative")
        if not isinstance(self.source_refs, tuple) or any(
            not isinstance(ref, str) or not ref.strip() for ref in self.source_refs
        ):
            raise ExposureOverlapError("source_refs must be a tuple of non-empty references")
        object.__setattr__(self, "_normalized_exposures", normalized)
        object.__setattr__(self, "_unresolved_gross", unresolved)

    @property
    def known_gross_inr(self) -> Decimal:
        return sum((abs(value) for value in self._normalized_exposures.values()), Decimal("0"))

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
    keys = tuple(sorted(set(a._normalized_exposures) & set(b._normalized_exposures)))
    common_gross = Decimal("0")
    signed_common = Decimal("0")
    for key in keys:
        left, right = a._normalized_exposures[key], b._normalized_exposures[key]
        shared = min(abs(left), abs(right))
        common_gross += shared
        if left and right:
            signed_common += shared if (left > 0) == (right > 0) else -shared

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
    "ExposureBook", "ExposureOverlap", "ExposureOverlapError", "compare_exposure_books",
]
