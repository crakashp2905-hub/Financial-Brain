"""Fail-closed final edge label from preregistration, signed forward evidence and gates."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math

from financial_brain.forward_edge.custody import (
    CustodyStatus,
    Receipt,
    TrustRoot,
    classify_custody,
)
from financial_brain.forward_edge.dependence import DependencePolicy
from financial_brain.forward_edge.evidence_stats import (
    execution_stress_gate,
    falsification_report,
    stress_robust,
)
from financial_brain.forward_edge.sample_size import SampleSizePlan
from .confirmation_bounds import confirmation_bounds


class EdgeConfirmationError(ValueError):
    """Malformed or inconsistent final confirmation evidence."""


@dataclass(frozen=True)
class SessionEvidence:
    session: str
    decision_manifest_sha256: str
    decision_at_utc: str
    entry_earliest_at_utc: str
    decision_receipt: Receipt | None
    daily_net_alpha: float
    after_cost_portfolio_utility: float
    outcome_at_utc: str
    next_entry_earliest_at_utc: str
    outcome_receipt: Receipt | None


def _digest(value: str, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise EdgeConfirmationError(f"{name} must be lowercase SHA-256")


def _utc(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise EdgeConfirmationError(f"{name} must be a timezone-aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EdgeConfirmationError(f"{name} must be a timezone-aware ISO timestamp")
    return parsed.astimezone(timezone.utc)


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def frozen_protocol_sha256(
    *,
    charter_sha256: str,
    scheduled_sessions: Sequence[str],
    sample_size_plan: SampleSizePlan,
    dependence_policy: DependencePolicy,
    risk_policy_sha256: str,
    execution_policy_sha256: str,
    integrity_policy_sha256: str,
) -> str:
    """Hash the pre-outcome charter, cohort, power, inference and control policies."""
    for name, value in (("charter_sha256", charter_sha256),
                        ("risk_policy_sha256", risk_policy_sha256),
                        ("execution_policy_sha256", execution_policy_sha256),
                        ("integrity_policy_sha256", integrity_policy_sha256)):
        _digest(value, name)
    sessions = tuple(scheduled_sessions)
    try:
        canonical = tuple(date.fromisoformat(item).isoformat() for item in sessions)
    except (TypeError, ValueError) as exc:
        raise EdgeConfirmationError("scheduled sessions must be ISO dates") from exc
    if not canonical or canonical != sessions or canonical != tuple(sorted(set(canonical))):
        raise EdgeConfirmationError("scheduled sessions must be nonempty, unique and ordered")
    return _canonical_hash({
        "charter_sha256": charter_sha256,
        "scheduled_sessions": canonical,
        "sample_size_plan_sha256": sample_size_plan.sha256(),
        "dependence_policy_sha256": dependence_policy.sha256(),
        "risk_policy_sha256": risk_policy_sha256,
        "execution_policy_sha256": execution_policy_sha256,
        "integrity_policy_sha256": integrity_policy_sha256,
    })


def _outcome_digest(row: SessionEvidence) -> str:
    return _canonical_hash({
        "session": row.session,
        "decision_manifest_sha256": row.decision_manifest_sha256,
        "daily_net_alpha": row.daily_net_alpha,
        "after_cost_portfolio_utility": row.after_cost_portfolio_utility,
    })


def _receipt_payload(receipt: Receipt | None) -> dict | None:
    if receipt is None:
        return None
    if not isinstance(receipt.anchored_at_utc, datetime):
        raise EdgeConfirmationError("receipt anchor must be a datetime")
    return {
        "provider_id": receipt.provider_id,
        "key_id": receipt.key_id,
        "digest_sha256": receipt.digest_sha256,
        "anchored_at_utc": receipt.anchored_at_utc.astimezone(timezone.utc).isoformat(),
        "signature_b64": receipt.signature_b64,
    }


def evaluate_edge_confirmation(
    *,
    scheduled_sessions: Sequence[str],
    charter_sha256: str,
    sample_size_plan: SampleSizePlan,
    dependence_policy: DependencePolicy,
    risk_policy_sha256: str,
    execution_policy_sha256: str,
    integrity_policy_sha256: str,
    registered_at_utc: str,
    registration_receipt: Receipt | None,
    first_observation_at_utc: str,
    session_evidence: Sequence[SessionEvidence],
    trust_roots: Mapping[tuple[str, str], TrustRoot],
    net_by_cost_multiplier: Mapping[float, float],
    net_by_cost_and_fill: Mapping[tuple[float, float], float] | None,
    fills_feasible: bool | None,
    fill_evidence_sha256: str | None,
    risk_pass: bool | None,
    risk_evidence_sha256: str | None,
    integrity_pass: bool | None,
    integrity_evidence_sha256: str | None,
    falsification_checks: Mapping[str, bool | None],
    completed_at_utc: str,
    release_deadline_utc: str,
    release_receipt: Receipt | None,
) -> dict:
    """Issue EDGE_CONFIRMED only after every policy and independently signed cohort gate passes.

    The registration receipt anchors the complete protocol before the first outcome.
    Each scheduled date needs a pre-entry decision receipt and a post-session outcome
    receipt against the pinned external trust roots. A final receipt attests the
    complete release bundle. Local/synthetic evidence without those receipts cannot
    produce the edge label.
    """
    sessions = tuple(scheduled_sessions)
    protocol_sha = frozen_protocol_sha256(
        charter_sha256=charter_sha256, scheduled_sessions=sessions,
        sample_size_plan=sample_size_plan, dependence_policy=dependence_policy,
        risk_policy_sha256=risk_policy_sha256,
        execution_policy_sha256=execution_policy_sha256,
        integrity_policy_sha256=integrity_policy_sha256,
    )
    first = _utc(first_observation_at_utc, "first_observation_at_utc")
    registered = _utc(registered_at_utc, "registered_at_utc")
    completed = _utc(completed_at_utc, "completed_at_utc")
    deadline = _utc(release_deadline_utc, "release_deadline_utc")
    reasons: list[str] = []
    if registered >= first or sample_size_plan.frozen_at_utc >= first.isoformat():
        reasons.append("PROTOCOL_NOT_FROZEN_BEFORE_OBSERVATION")
    if sample_size_plan.first_observation_at_utc != first.isoformat():
        reasons.append("SAMPLE_PLAN_COHORT_MISMATCH")
    if registration_receipt is None:
        registration_status = CustodyStatus.LOCAL_ONLY
    else:
        registration_status = classify_custody(
            manifest_sha256=protocol_sha, decision_at_utc=registered,
            entry_earliest_at_utc=first, receipt=registration_receipt,
            trust_roots=trust_roots,
        )
    if registration_status is not CustodyStatus.VERIFIED:
        reasons.append("PREREGISTRATION_RECEIPT_NOT_VERIFIED")

    by_session: dict[str, SessionEvidence] = {}
    for row in session_evidence:
        if row.session in by_session:
            raise EdgeConfirmationError("duplicate session evidence")
        if row.session not in sessions:
            raise EdgeConfirmationError("session evidence is outside the frozen schedule")
        _digest(row.decision_manifest_sha256, "decision_manifest_sha256")
        if not math.isfinite(row.daily_net_alpha) or not math.isfinite(row.after_cost_portfolio_utility):
            raise EdgeConfirmationError("session outcomes must be finite")
        by_session[row.session] = row
    missing = [day for day in sessions if day not in by_session]
    if missing:
        reasons.append("SCHEDULED_SESSION_EVIDENCE_MISSING")
    verified_sessions = 0
    session_receipt_status: dict[str, dict[str, str]] = {}
    for day, row in by_session.items():
        decision = classify_custody(
            manifest_sha256=row.decision_manifest_sha256,
            decision_at_utc=_utc(row.decision_at_utc, "decision_at_utc"),
            entry_earliest_at_utc=_utc(row.entry_earliest_at_utc, "entry_earliest_at_utc"),
            receipt=row.decision_receipt, trust_roots=trust_roots,
        )
        outcome_digest = _outcome_digest(row)
        outcome = classify_custody(
            manifest_sha256=outcome_digest,
            decision_at_utc=_utc(row.outcome_at_utc, "outcome_at_utc"),
            entry_earliest_at_utc=_utc(row.next_entry_earliest_at_utc,
                                       "next_entry_earliest_at_utc"),
            receipt=row.outcome_receipt, trust_roots=trust_roots,
        )
        session_receipt_status[day] = {"decision": decision.value, "outcome": outcome.value}
        if decision is CustodyStatus.VERIFIED and outcome is CustodyStatus.VERIFIED:
            verified_sessions += 1
    if verified_sessions != len(sessions):
        reasons.append("INDEPENDENT_SESSION_RECEIPTS_INCOMPLETE")
    required_sessions = max(60, sample_size_plan.required_sessions)
    if len(sessions) < required_sessions or verified_sessions < required_sessions:
        reasons.append("SAMPLE_SIZE_NOT_MET")

    alpha_by_day = {day: by_session[day].daily_net_alpha for day in sessions if day in by_session}
    utility_by_day = {day: by_session[day].after_cost_portfolio_utility for day in sessions if day in by_session}
    bounds = confirmation_bounds(
        scheduled_sessions=sessions, daily_net_alpha=alpha_by_day,
        after_cost_portfolio_utility=utility_by_day, policy=dependence_policy,
    )
    if bounds["status"] != "CONFIRMED":
        reasons.append("ROBUST_LOWER_BOUNDS_NOT_POSITIVE")

    cost_pass = stress_robust(dict(net_by_cost_multiplier))
    execution_stress = execution_stress_gate(
        dict(net_by_cost_and_fill) if net_by_cost_and_fill is not None else None
    )
    if not cost_pass:
        reasons.append("COST_STRESS_FAILED_OR_MISSING")
    if execution_stress["status"] != "PASS":
        reasons.append("EXECUTION_STRESS_FAILED_OR_MISSING")
    for label, value, digest in (
        ("fills", fills_feasible, fill_evidence_sha256),
        ("risk", risk_pass, risk_evidence_sha256),
        ("integrity", integrity_pass, integrity_evidence_sha256),
    ):
        if value is not True:
            reasons.append(f"{label.upper()}_GATE_NOT_PASSED")
        try:
            _digest(digest, f"{label}_evidence_sha256")
        except EdgeConfirmationError:
            reasons.append(f"{label.upper()}_EVIDENCE_DIGEST_MISSING")
    falsification = falsification_report(dict(falsification_checks))
    if falsification["status"] != "NO_FALSIFICATION_FOUND":
        reasons.append("FALSIFICATION_" + falsification["status"])

    evidence_payload = {
        "protocol_sha256": protocol_sha,
        "scheduled_sessions": sessions,
        "registration_status": registration_status.value,
        "registration_receipt": _receipt_payload(registration_receipt),
        "registered_at_utc": registered.isoformat(),
        "first_observation_at_utc": first.isoformat(),
        "trust_roots": [
            {
                "provider_id": root.provider_id,
                "key_id": root.key_id,
                "public_key_b64": root.public_key_b64,
            }
            for _, root in sorted(trust_roots.items())
        ],
        "session_receipt_status": session_receipt_status,
        "sessions": {
            day: {
                "decision_manifest_sha256": row.decision_manifest_sha256,
                "decision_at_utc": _utc(row.decision_at_utc, "decision_at_utc").isoformat(),
                "entry_earliest_at_utc": _utc(row.entry_earliest_at_utc,
                                                "entry_earliest_at_utc").isoformat(),
                "decision_receipt": _receipt_payload(row.decision_receipt),
                "daily_net_alpha": row.daily_net_alpha,
                "after_cost_portfolio_utility": row.after_cost_portfolio_utility,
                "outcome_sha256": _outcome_digest(row),
                "outcome_at_utc": _utc(row.outcome_at_utc, "outcome_at_utc").isoformat(),
                "next_entry_earliest_at_utc": _utc(
                    row.next_entry_earliest_at_utc, "next_entry_earliest_at_utc"
                ).isoformat(),
                "outcome_receipt": _receipt_payload(row.outcome_receipt),
            }
            for day, row in sorted(by_session.items())
        },
        "verified_sessions": verified_sessions,
        "required_sessions": required_sessions,
        "confirmation_bounds": bounds,
        "net_by_cost_multiplier": dict(sorted(net_by_cost_multiplier.items())),
        "net_by_cost_and_fill": (
            {f"{cost:g}x@{fill:.0%}": value
             for (cost, fill), value in sorted((net_by_cost_and_fill or {}).items())}
        ),
        "execution_stress": execution_stress,
        "fills_feasible": fills_feasible,
        "fill_evidence_sha256": fill_evidence_sha256,
        "risk_pass": risk_pass,
        "risk_evidence_sha256": risk_evidence_sha256,
        "integrity_pass": integrity_pass,
        "integrity_evidence_sha256": integrity_evidence_sha256,
        "falsification": falsification,
    }
    release_bundle_sha = _canonical_hash(evidence_payload)
    if completed >= deadline:
        reasons.append("RELEASE_RECEIPT_WINDOW_INVALID")
    release_status = (classify_custody(
        manifest_sha256=release_bundle_sha, decision_at_utc=completed,
        entry_earliest_at_utc=deadline, receipt=release_receipt,
        trust_roots=trust_roots,
    ) if deadline > completed else CustodyStatus.CONFLICTED)
    if release_status is not CustodyStatus.VERIFIED:
        reasons.append("RELEASE_BUNDLE_RECEIPT_NOT_VERIFIED")
    unique_reasons = list(dict.fromkeys(reasons))
    return {
        "verdict": "EDGE_CONFIRMED" if not unique_reasons else "NOT_CONFIRMED",
        "reasons": unique_reasons,
        "protocol_sha256": protocol_sha,
        "release_bundle_sha256": release_bundle_sha,
        "registration_receipt_status": registration_status.value,
        "independently_verified_sessions": verified_sessions,
        "required_sessions": required_sessions,
        "missing_sessions": missing,
        "session_receipt_status": session_receipt_status,
        "confirmation_bounds": bounds,
        "cost_stress_pass": cost_pass,
        "execution_stress": execution_stress,
        "fills_feasible": fills_feasible is True,
        "risk_pass": risk_pass is True,
        "integrity_pass": integrity_pass is True,
        "falsification": falsification,
        "release_receipt_status": release_status.value,
        "claim_scope": "A code verdict only; external trust roots, provider rights, data quality and economic evidence must be independently established.",
    }
