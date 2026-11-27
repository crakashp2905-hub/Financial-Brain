"""Fail-closed audit contract for open-position exit-policy and monitor coverage."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Iterable


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ExitCoverageError(ValueError):
    """Invalid or ambiguous position/monitor evidence."""


class ExitProduct(StrEnum):
    EXCHANGE_TRADED = "EXCHANGE_TRADED"
    NAV_MUTUAL_FUND = "NAV_MUTUAL_FUND"


@dataclass(frozen=True, slots=True)
class OpenPositionForExitCoverage:
    position_id: str
    instrument_id: str
    product: ExitProduct
    quantity: Decimal

    def __post_init__(self) -> None:
        if not self.position_id.strip() or not self.instrument_id.strip():
            raise ExitCoverageError("position and instrument IDs are required")
        if not isinstance(self.product, ExitProduct):
            raise ExitCoverageError("product must use a supported ExitProduct")
        try:
            quantity = Decimal(self.quantity)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ExitCoverageError("open quantity must be a finite nonzero decimal") from exc
        if not quantity.is_finite() or quantity == 0:
            raise ExitCoverageError("open quantity must be a finite nonzero decimal")
        object.__setattr__(self, "quantity", quantity)


@dataclass(frozen=True, slots=True)
class ExitCoverageEvidence:
    position_id: str
    monitor_id: str
    last_processed_event_id: str
    replayed_through_sequence: int
    journal_hash_chain_valid: bool
    exit_policy_sha256: str | None = None
    exception_code: str | None = None
    exception_reason: str | None = None
    exception_evidence_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ExitCoverageGap:
    position_id: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExitCoverageReport:
    open_position_count: int
    covered_position_count: int
    gaps: tuple[ExitCoverageGap, ...]

    @property
    def complete(self) -> bool:
        return not self.gaps


def audit_exit_coverage(*, positions: Iterable[OpenPositionForExitCoverage],
                        evidence: Iterable[ExitCoverageEvidence]) -> ExitCoverageReport:
    """Require every open position to have a valid replayable monitor and valid exit basis.

    Exchange-traded positions require a hashed exit policy. NAV-settled mutual funds instead
    require a reasoned, hash-pinned NAV exception; they still require a replayable monitor.
    This pure audit does not claim that upstream books have supplied a complete position set.
    """
    position_rows = tuple(positions)
    evidence_rows = tuple(evidence)
    by_position: dict[str, OpenPositionForExitCoverage] = {}
    for position in position_rows:
        if position.position_id in by_position:
            raise ExitCoverageError("duplicate open position ID")
        by_position[position.position_id] = position
    by_evidence: dict[str, ExitCoverageEvidence] = {}
    for evidence_row in evidence_rows:
        if not evidence_row.position_id.strip() or evidence_row.position_id in by_evidence:
            raise ExitCoverageError("exit coverage evidence IDs must be present and unique")
        by_evidence[evidence_row.position_id] = evidence_row

    gaps: list[ExitCoverageGap] = []
    for position in sorted(position_rows, key=lambda row: row.position_id):
        row = by_evidence.get(position.position_id)
        reasons: list[str] = []
        if row is None:
            gaps.append(ExitCoverageGap(position.position_id, ("MISSING_EXIT_COVERAGE",)))
            continue
        if (not row.monitor_id.strip() or not row.last_processed_event_id.strip()
                or type(row.replayed_through_sequence) is not int or row.replayed_through_sequence < 1):
            reasons.append("MISSING_CRASH_RESUMABLE_MONITOR")
        if row.journal_hash_chain_valid is not True:
            reasons.append("MONITOR_JOURNAL_UNVERIFIED")
        policy_valid = row.exit_policy_sha256 is not None and _SHA256.fullmatch(row.exit_policy_sha256) is not None
        exception_valid = (
            row.exception_code == "NAV_SETTLEMENT"
            and isinstance(row.exception_reason, str) and bool(row.exception_reason.strip())
            and isinstance(row.exception_evidence_sha256, str)
            and _SHA256.fullmatch(row.exception_evidence_sha256) is not None
        )
        if position.product is ExitProduct.EXCHANGE_TRADED and not policy_valid:
            reasons.append("MISSING_OR_INVALID_EXIT_POLICY")
        elif position.product is ExitProduct.NAV_MUTUAL_FUND and not exception_valid:
            reasons.append("MISSING_AUDITABLE_NAV_EXIT_EXCEPTION")
        if position.product is ExitProduct.EXCHANGE_TRADED and row.exception_code is not None:
            reasons.append("UNEXPECTED_PRODUCT_EXIT_EXCEPTION")
        if reasons:
            gaps.append(ExitCoverageGap(position.position_id, tuple(reasons)))
    orphan_ids = sorted(set(by_evidence) - set(by_position))
    gaps.extend(ExitCoverageGap(position_id, ("ORPHAN_EXIT_COVERAGE_EVIDENCE",)) for position_id in orphan_ids)
    uncovered_ids = {gap.position_id for gap in gaps if gap.position_id in by_position}
    return ExitCoverageReport(len(position_rows), len(position_rows) - len(uncovered_ids), tuple(gaps))


def audit_canonical_snapshot_exit_coverage(*, snapshot: dict[str, object],
                                           evidence: Iterable[ExitCoverageEvidence]
                                           ) -> ExitCoverageReport:
    """Audit the open positions projected by ``CanonicalPortfolioLedger.snapshot``.

    Position identities include asset class, instrument and sleeve so parallel sleeves
    cannot accidentally share one monitor receipt. This only covers the supplied account
    snapshot; callers must still aggregate every account/book in scope.
    """
    rows = snapshot.get("positions")
    if not isinstance(rows, list):
        raise ExitCoverageError("canonical portfolio snapshot must contain a positions list")
    positions: list[OpenPositionForExitCoverage] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ExitCoverageError("canonical snapshot position rows must be objects")
        try:
            asset_class = str(row["asset_class"])
            instrument = str(row["instrument_id"])
            sleeve = str(row["sleeve_id"])
            quantity = Decimal(str(row["quantity"]))
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise ExitCoverageError("canonical snapshot position identity or quantity is invalid") from exc
        if not quantity.is_finite():
            raise ExitCoverageError("canonical snapshot position quantity must be finite")
        if quantity == 0:
            continue
        if asset_class not in {"EQUITY", "ETF", "OPTION", "MUTUAL_FUND"}:
            raise ExitCoverageError(f"unsupported canonical asset class: {asset_class}")
        product = (ExitProduct.NAV_MUTUAL_FUND if asset_class == "MUTUAL_FUND"
                   else ExitProduct.EXCHANGE_TRADED)
        position_id = f"{asset_class}:{instrument}:{sleeve}"
        positions.append(OpenPositionForExitCoverage(position_id, instrument, product, quantity))
    return audit_exit_coverage(positions=positions, evidence=evidence)


__all__ = [
    "ExitCoverageEvidence",
    "ExitCoverageError",
    "ExitCoverageGap",
    "ExitCoverageReport",
    "ExitProduct",
    "OpenPositionForExitCoverage",
    "audit_canonical_snapshot_exit_coverage",
    "audit_exit_coverage",
]
