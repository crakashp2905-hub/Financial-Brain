"""Evidence labels shared by paper reports and exports.

Historical replay, deterministic fixtures, local paper, prospectively observed sessions and independently
attested forward sessions are distinct classes. This module labels evidence; it does not create or verify the facts.
"""
from __future__ import annotations

from financial_brain.forward_edge.custody import CustodyStatus
from financial_brain.kernel.evidence import EvidenceClass


class EvidenceLabelError(ValueError):
    pass


_FORWARD_CLAIMS = {
    "FORWARD_PAPER", "PAPER_FORWARD", "INDEPENDENT_FORWARD", "INDEPENDENTLY_ATTESTED_FORWARD",
    "DEPLOYMENT_VALIDATED", "CONFIRMED", "PROMOTION_GRADE", "FORWARD-PAPER EVIDENCE",
    "CONFIRMED STRATEGY",
}


_MODES = {
    "shadow": EvidenceClass.EXPLORATORY_SHADOW,
    "replay": EvidenceClass.HISTORICAL_REPLAY,
    "fixture": EvidenceClass.DETERMINISTIC_FIXTURE,
    "fixture_replay": EvidenceClass.DETERMINISTIC_FIXTURE,
    "historical": EvidenceClass.HISTORICAL_REPLAY,
    "historical_replay": EvidenceClass.HISTORICAL_REPLAY,
    "local_paper": EvidenceClass.LOCAL_PAPER,
    "paper": EvidenceClass.LOCAL_PAPER,
    "live": EvidenceClass.LOCAL_PAPER,  # legacy runner mode means read-only observed quotes + simulated fills
}


def classify(mode: str, *, market_observed: bool = False, pre_entry_sealed: bool = False,
             session_complete: bool = False, custody_status: CustodyStatus | None = None) -> EvidenceClass:
    """Classify without allowing caller-supplied report labels to override the execution/evidence mode."""
    normalized = mode.strip().lower() if isinstance(mode, str) else ""
    base = _MODES.get(normalized)
    if base is None:
        raise EvidenceLabelError(f"unsupported evidence mode: {mode!r}")
    # Replay and fixture evidence cannot become prospective by attaching custody after the fact.
    if base in (EvidenceClass.HISTORICAL_REPLAY, EvidenceClass.DETERMINISTIC_FIXTURE,
                EvidenceClass.EXPLORATORY_SHADOW):
        return base
    if market_observed and pre_entry_sealed and session_complete:
        if custody_status is CustodyStatus.VERIFIED:
            return EvidenceClass.INDEPENDENTLY_ATTESTED_FORWARD
        return EvidenceClass.PROSPECTIVELY_OBSERVED_SESSION
    return EvidenceClass.LOCAL_PAPER


def metadata(mode: str, *, market_observed: bool = False, pre_entry_sealed: bool = False,
             session_complete: bool = False, custody_status: CustodyStatus | None = None) -> dict[str, object]:
    label = classify(mode, market_observed=market_observed, pre_entry_sealed=pre_entry_sealed,
                     session_complete=session_complete, custody_status=custody_status)
    return {
        "evidence_class": label.value,
        "independently_attested": label is EvidenceClass.INDEPENDENTLY_ATTESTED_FORWARD,
    }


def stamp_report(report: dict, mode: str, *, market_observed: bool = False, pre_entry_sealed: bool = False,
                 session_complete: bool = False, custody_status: CustodyStatus | None = None) -> dict:
    """Return a report with a derived evidence label; reject conflicting upstream labels."""
    if not isinstance(report, dict):
        raise EvidenceLabelError("report must be an object")
    derived = metadata(mode, market_observed=market_observed, pre_entry_sealed=pre_entry_sealed,
                       session_complete=session_complete, custody_status=custody_status)
    existing = report.get("evidence_class")
    if existing is not None and existing != derived["evidence_class"]:
        raise EvidenceLabelError("report evidence label conflicts with the execution mode")
    if report.get("independently_attested") is True and not derived["independently_attested"]:
        raise EvidenceLabelError("report cannot claim independent attestation without verified session evidence")
    if not derived["independently_attested"]:
        for key in ("tier", "evidence_tier", "claim", "verdict"):
            value = report.get(key)
            if isinstance(value, str) and value.strip().upper() in _FORWARD_CLAIMS:
                raise EvidenceLabelError(f"report field {key!r} makes a forward claim unsupported by its evidence mode")
    return {**report, **derived}
