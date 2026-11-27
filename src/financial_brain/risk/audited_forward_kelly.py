"""Conservative Kelly sizing from externally signed, forward net-return evidence only."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class AuditedKellyError(ValueError):
    """Forward return evidence, auditor signature or Kelly policy is invalid."""


def _aware(stamp: datetime) -> bool:
    return stamp.tzinfo is not None and stamp.utcoffset() is not None


def _valid_sha256(value: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{64}", value) is not None


@dataclass(frozen=True, slots=True)
class AuditedForwardReturns:
    strategy_id: str
    strategy_version_sha256: str
    cohort_id: str
    cohort_manifest_sha256: str
    execution_costs_sha256: str
    audit_report_sha256: str
    auditor_id: str
    training_through: datetime
    first_eligible_session: date
    forward_start: datetime
    forward_end: datetime
    audited_at: datetime
    net_period_returns: tuple[float, ...]
    net_of_all_costs: bool
    includes_zero_return_periods: bool
    signature_b64: str

    def signed_bytes(self) -> bytes:
        payload = {
            "strategy_id": self.strategy_id,
            "strategy_version_sha256": self.strategy_version_sha256,
            "cohort_id": self.cohort_id,
            "cohort_manifest_sha256": self.cohort_manifest_sha256,
            "execution_costs_sha256": self.execution_costs_sha256,
            "audit_report_sha256": self.audit_report_sha256,
            "auditor_id": self.auditor_id,
            "training_through": self.training_through.astimezone(timezone.utc).isoformat(),
            "first_eligible_session": self.first_eligible_session.isoformat(),
            "forward_start": self.forward_start.astimezone(timezone.utc).isoformat(),
            "forward_end": self.forward_end.astimezone(timezone.utc).isoformat(),
            "audited_at": self.audited_at.astimezone(timezone.utc).isoformat(),
            "net_period_returns": self.net_period_returns,
            "net_of_all_costs": self.net_of_all_costs,
            "includes_zero_return_periods": self.includes_zero_return_periods,
        }
        try:
            return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                              allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise AuditedKellyError("signed return evidence is not canonical JSON") from exc

    def digest(self) -> str:
        return hashlib.sha256(self.signed_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class AuditedKellyPolicy:
    policy_id: str
    frozen_at: datetime
    first_eligible_session: date
    minimum_periods: int = 60
    fractional_kelly: float = 0.10
    maximum_position_fraction: float = 0.10
    one_sided_confidence_z: float = 1.645


@dataclass(frozen=True, slots=True)
class AuditedKellyDecision:
    strategy_id: str
    forward_evidence_sha256: str
    periods: int
    mean_net_return: float
    periodic_variance: float
    hac_standard_error: float
    conservative_mean_lcb: float
    conservative_kelly_fraction: float
    applied_position_fraction: float
    maximum_position_fraction: float


def _validate_policy(policy: AuditedKellyPolicy, *, decision_at: datetime) -> None:
    if not policy.policy_id.strip() or not _aware(policy.frozen_at) or not _aware(decision_at):
        raise AuditedKellyError("policy identity and aware timestamps are required")
    if policy.frozen_at >= decision_at or policy.frozen_at.date() >= policy.first_eligible_session:
        raise AuditedKellyError("Kelly policy must be frozen before the eligible cohort and decision")
    if (type(policy.minimum_periods) is not int or policy.minimum_periods < 30
            or not math.isfinite(policy.fractional_kelly)
            or not 0 < policy.fractional_kelly <= 0.25
            or not math.isfinite(policy.maximum_position_fraction)
            or not 0 < policy.maximum_position_fraction <= 0.25
            or not math.isfinite(policy.one_sided_confidence_z)
            or policy.one_sided_confidence_z < 1.645):
        raise AuditedKellyError("Kelly sample, fraction cap or confidence policy is unsafe")


def _hac_mean_standard_error(returns: tuple[float, ...]) -> float:
    """Newey-West Bartlett standard error with deterministic rule-of-thumb truncation lag."""
    n = len(returns)
    mean = math.fsum(returns) / n
    centered = tuple(value - mean for value in returns)
    lag = min(n - 1, max(1, int(4 * (n / 100) ** (2 / 9))))
    long_run_variance = math.fsum(value * value for value in centered) / n
    for offset in range(1, lag + 1):
        covariance = math.fsum(centered[index] * centered[index - offset]
                               for index in range(offset, n)) / n
        weight = 1 - offset / (lag + 1)
        long_run_variance += 2 * weight * covariance
    return math.sqrt(max(0.0, long_run_variance) / n)


def size_from_audited_forward_returns(
    evidence: AuditedForwardReturns,
    policy: AuditedKellyPolicy,
    *,
    decision_at: datetime,
    trusted_auditor_public_key_b64: str,
) -> AuditedKellyDecision:
    """Verify the independent signature, then apply lower-bound Kelly sizing and hard caps.

    Returns are periodic NAV returns net of all costs, with zero-return periods retained.
    The independently trusted public key must be pinned by the caller. This function
    cannot establish that a key belongs to an independent auditor by itself.
    """
    _validate_policy(policy, decision_at=decision_at)
    timestamps = (evidence.training_through, evidence.forward_start,
                  evidence.forward_end, evidence.audited_at)
    if not all(_aware(stamp) for stamp in timestamps):
        raise AuditedKellyError("forward evidence timestamps must be timezone aware")
    if (not evidence.strategy_id.strip() or not evidence.cohort_id.strip()
            or not evidence.auditor_id.strip()):
        raise AuditedKellyError("strategy, cohort and auditor identities are required")
    hashes = (evidence.strategy_version_sha256, evidence.cohort_manifest_sha256,
              evidence.execution_costs_sha256, evidence.audit_report_sha256)
    if not all(_valid_sha256(value) for value in hashes):
        raise AuditedKellyError("strategy, cohort, costs and audit artifact hashes are required")
    if not evidence.net_of_all_costs or not evidence.includes_zero_return_periods:
        raise AuditedKellyError("Kelly sizing requires net returns and zero-return/no-fill periods")
    if not (evidence.training_through < evidence.forward_start <= evidence.forward_end
            < evidence.audited_at < decision_at):
        raise AuditedKellyError("forward outcomes must follow training and audit before the decision")
    if evidence.forward_start.date() < evidence.first_eligible_session:
        raise AuditedKellyError("forward outcome window predates its eligible cohort")
    if evidence.first_eligible_session < policy.first_eligible_session:
        raise AuditedKellyError("outcome cohort predates the frozen Kelly policy")
    if len(evidence.net_period_returns) < policy.minimum_periods:
        raise AuditedKellyError("insufficient audited forward periods for Kelly sizing")
    if not all(math.isfinite(value) and value > -1 for value in evidence.net_period_returns):
        raise AuditedKellyError("forward net returns must be finite simple returns above -100%")

    try:
        public_key_bytes = base64.b64decode(trusted_auditor_public_key_b64, validate=True)
        signature = base64.b64decode(evidence.signature_b64, validate=True)
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        public_key.verify(signature, evidence.signed_bytes())
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise AuditedKellyError("forward outcome audit signature is invalid or untrusted") from exc

    returns = evidence.net_period_returns
    n = len(returns)
    mean = math.fsum(returns) / n
    variance = math.fsum((value - mean) ** 2 for value in returns) / (n - 1)
    standard_error = _hac_mean_standard_error(returns)
    lower_bound = max(0.0, mean - policy.one_sided_confidence_z * standard_error)
    kelly = lower_bound / variance if variance > 0 else 0.0
    applied = min(policy.maximum_position_fraction, policy.fractional_kelly * kelly)
    return AuditedKellyDecision(
        strategy_id=evidence.strategy_id,
        forward_evidence_sha256=evidence.digest(),
        periods=n,
        mean_net_return=mean,
        periodic_variance=variance,
        hac_standard_error=standard_error,
        conservative_mean_lcb=lower_bound,
        conservative_kelly_fraction=kelly,
        applied_position_fraction=applied,
        maximum_position_fraction=policy.maximum_position_fraction,
    )
