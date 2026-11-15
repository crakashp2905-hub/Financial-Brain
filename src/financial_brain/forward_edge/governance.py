"""Model learning and champion/challenger governance (FE-0124 .. FE-0131). Offline; no model is trained here."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re

from financial_brain.forward_edge.approvals import Approval, ApprovalError, validate_approval


class GovernanceError(ValueError):
    pass


# ---- FE-0124 outcome maturation ----

class MaturationQueue:
    """A label is released only after the full exit plus an embargo; unfinished outcomes never reach training."""

    def __init__(self, embargo: timedelta) -> None:
        self.embargo = embargo
        self._items: dict[str, datetime] = {}

    def add(self, example_id: str, exit_at: datetime) -> None:
        if exit_at.tzinfo is None:
            raise GovernanceError("exit time must be timezone-aware")
        if example_id in self._items:
            raise GovernanceError("example already queued")
        self._items[example_id] = exit_at.astimezone(timezone.utc)

    def mature(self, now: datetime) -> list[str]:
        return sorted(k for k, t in self._items.items() if t + self.embargo <= now.astimezone(timezone.utc))


# ---- FE-0125 reproducible training lineage ----

def model_id(*, data_sha256: str, feature_schema_sha256: str, params: dict[str, str], seed: int, code_sha256: str) -> str:
    body = json.dumps({"data": data_sha256, "schema": feature_schema_sha256, "params": params, "seed": seed,
                       "code": code_sha256}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ---- FE-0126 contamination guard ----

def assert_search_data_clear(search_dates: set[date], locked_cohort_dates: set[date]) -> None:
    overlap = sorted(search_dates & locked_cohort_dates)
    if overlap:
        raise GovernanceError(f"hyperparameter search touches a locked cohort: {overlap[0]}")


# ---- FE-0127 challenger vs champion ----

def challenger_beats_champion(*, mean_diff_net: float, se_diff: float, candidates_tested: int, z_single: float = 1.96) -> bool:
    """Net, aligned-capital difference must clear a Bonferroni-widened bound for the number of candidates tested."""
    if candidates_tested < 1 or se_diff <= 0:
        raise GovernanceError("need a positive standard error and at least one candidate")
    from statistics import NormalDist
    alpha = 0.05 / candidates_tested
    z = NormalDist().inv_cdf(1 - alpha / 2)
    return mean_diff_net - z * se_diff > 0


# ---- FE-0128 promotion restriction ----

def promotion_allowed(*, status: str, preauthorized: bool, verified_sessions: int, min_sessions: int = 60) -> tuple[bool, str]:
    if not preauthorized:
        return False, "NOT_PREAUTHORIZED"
    if status not in ("FROZEN_CHALLENGER", "VERIFIED"):
        return False, "STATUS_NOT_PROMOTABLE"
    if verified_sessions < min_sessions:
        return False, "NO_TRUSTED_RECEIPT_COVERAGE"
    return True, "OK"


# ---- FE-0129 human approval for economic risk ----

ECONOMIC_KINDS = ("CAPITAL", "LEVERAGE", "MICRO_RISK_BUDGET", "LIVE_CASH_ENABLE")


def authorize_economic_change(kind: str, subject: str, scope_sha256: str, approval: Approval | None, now: datetime) -> None:
    if kind not in ECONOMIC_KINDS:
        raise GovernanceError("not an economic-risk change kind")
    if kind == "CAPITAL":
        raise GovernanceError("family evidence gate required; use evaluate_family_action")
    if approval is None:
        raise GovernanceError("a human approval is required")
    try:
        validate_approval(approval, kind=kind, subject=subject, scope_sha256=scope_sha256, now=now)
    except ApprovalError as exc:
        raise GovernanceError(f"change refused: {exc}") from exc


# ---- PE-0170 family-specific capital and paper-automation gate ----

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class FamilyEvidence:
    """Hashable family-level evidence needed before a paper-risk increase or automation."""

    family_id: str
    status: str
    strategy_sha256: str
    frozen_protocol_sha256: str
    evidence_manifest_sha256: str
    protocol_frozen: bool
    protocol_frozen_at: datetime
    cohort_started_at: datetime
    verified_sessions: int
    receipt_sessions: int
    candidate_count: int
    multiplicity_adjusted: bool
    cost_stress_passed: bool
    matched_control_lcb_bps: Decimal
    net_alpha_lcb_bps: Decimal

    def __post_init__(self) -> None:
        if not self.family_id.strip():
            raise GovernanceError("family_id is required")
        for name in ("strategy_sha256", "frozen_protocol_sha256", "evidence_manifest_sha256"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise GovernanceError(f"{name} must be a lowercase SHA-256")
        for label, stamp in (("protocol_frozen_at", self.protocol_frozen_at),
                             ("cohort_started_at", self.cohort_started_at)):
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise GovernanceError(f"{label} must be timezone-aware")
        for name, value in (("matched_control_lcb_bps", self.matched_control_lcb_bps),
                            ("net_alpha_lcb_bps", self.net_alpha_lcb_bps)):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise GovernanceError(f"{name} must be a finite Decimal")

    @property
    def evidence_sha256(self) -> str:
        body = {
            **self.__dict__,
            "protocol_frozen_at": self.protocol_frozen_at.astimezone(timezone.utc).isoformat(),
            "cohort_started_at": self.cohort_started_at.astimezone(timezone.utc).isoformat(),
            "matched_control_lcb_bps": str(self.matched_control_lcb_bps),
            "net_alpha_lcb_bps": str(self.net_alpha_lcb_bps),
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FamilyActionVerdict:
    approved: bool
    action: str
    family_id: str
    evidence_sha256: str
    scope_sha256: str
    reasons: tuple[str, ...]
    verdict_sha256: str


def family_action_scope_sha256(*, evidence: FamilyEvidence, action: str,
                               current_capital_inr: Decimal,
                               requested_capital_inr: Decimal,
                               execution_mode: str = "PAPER") -> str:
    """Bind owner approval to one family, evidence set, action and exact capital scope."""
    body = {
        "family_id": evidence.family_id, "family_evidence_sha256": evidence.evidence_sha256,
        "action": action,
        "current_capital_inr": format(Decimal(current_capital_inr).normalize(), "f"),
        "requested_capital_inr": format(Decimal(requested_capital_inr).normalize(), "f"),
        "execution_mode": execution_mode,
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                           allow_nan=False).encode("utf-8")).hexdigest()


def evaluate_family_action(*, evidence: FamilyEvidence, action: str,
                           current_capital_inr: Decimal,
                           requested_capital_inr: Decimal,
                           owner_approval: Approval | None,
                           now: datetime, execution_mode: str = "PAPER",
                           min_verified_sessions: int = 60) -> FamilyActionVerdict:
    """Require frozen, multiplicity-adjusted, costed family proof and scoped human approval.

    This gate only authorizes paper capital changes or paper automation. Live execution is
    deliberately unavailable here and must pass its separate compliance/rehearsal gate.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise GovernanceError("now must be timezone-aware")
    try:
        current_capital = Decimal(current_capital_inr)
        requested_capital = Decimal(requested_capital_inr)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise GovernanceError("capital amounts must be finite positive decimals") from exc
    if (not current_capital.is_finite() or current_capital < 0 or
            not requested_capital.is_finite() or requested_capital <= 0):
        raise GovernanceError("current capital must be non-negative and requested capital finite positive")
    if type(min_verified_sessions) is not int or min_verified_sessions < 1:
        raise GovernanceError("min_verified_sessions must be a positive integer")
    if action not in {"CAPITAL_INCREASE", "INITIAL_ALLOCATION", "AUTO_EXECUTION_ENABLE"}:
        raise GovernanceError("unknown family action")
    if action != "INITIAL_ALLOCATION" and current_capital <= 0:
        raise GovernanceError("non-initial family action requires positive current capital")
    reasons: set[str] = set()
    if execution_mode != "PAPER":
        reasons.add("LIVE_MODE_REQUIRES_SEPARATE_RELEASE_GATE")
    if action == "CAPITAL_INCREASE" and requested_capital <= current_capital:
        reasons.add("REQUEST_IS_NOT_A_CAPITAL_INCREASE")
    if action == "INITIAL_ALLOCATION" and current_capital != 0:
        reasons.add("INITIAL_ALLOCATION_REQUIRES_ZERO_PRIOR_CAPITAL")
    if action == "INITIAL_ALLOCATION" and execution_mode != "PAPER":
        reasons.add("INITIAL_ALLOCATION_REQUIRES_PAPER_MODE")
    if action == "AUTO_EXECUTION_ENABLE" and requested_capital > current_capital:
        reasons.add("AUTO_EXECUTION_CANNOT_INCREASE_CAPITAL")
    if evidence.status != "VERIFIED":
        reasons.add("FAMILY_STATUS_NOT_VERIFIED")
    if evidence.protocol_frozen is not True or not (
        evidence.protocol_frozen_at.astimezone(timezone.utc)
        < evidence.cohort_started_at.astimezone(timezone.utc)
    ):
        reasons.add("FROZEN_PROTOCOL_NOT_ATTESTED_BEFORE_COHORT")
    if (type(evidence.verified_sessions) is not int
            or evidence.verified_sessions < min_verified_sessions):
        reasons.add("INSUFFICIENT_FAMILY_VERIFIED_SESSIONS")
    if (type(evidence.receipt_sessions) is not int
            or evidence.receipt_sessions != evidence.verified_sessions):
        reasons.add("FAMILY_RECEIPT_COVERAGE_INCOMPLETE")
    if type(evidence.candidate_count) is not int or evidence.candidate_count < 1:
        reasons.add("FAMILY_CANDIDATE_DENOMINATOR_MISSING")
    if evidence.multiplicity_adjusted is not True:
        reasons.add("FAMILY_MULTIPLICITY_NOT_ADJUSTED")
    if evidence.cost_stress_passed is not True:
        reasons.add("FAMILY_COST_STRESS_FAILED")
    if evidence.matched_control_lcb_bps <= 0:
        reasons.add("FAMILY_MATCHED_CONTROL_NOT_POSITIVE")
    if evidence.net_alpha_lcb_bps <= 0:
        reasons.add("FAMILY_NET_ALPHA_NOT_POSITIVE")

    approval_kind = "CAPITAL" if action == "CAPITAL_INCREASE" else "STRATEGY_ENROLLMENT"
    scope = family_action_scope_sha256(
        evidence=evidence, action=action, current_capital_inr=current_capital,
        requested_capital_inr=requested_capital, execution_mode=execution_mode,
    )
    if owner_approval is None:
        reasons.add("SCOPED_HUMAN_OWNER_APPROVAL_REQUIRED")
    else:
        try:
            validate_approval(owner_approval, kind=approval_kind, subject=evidence.family_id,
                              scope_sha256=scope, now=now)
        except ApprovalError as exc:
            reasons.add(f"OWNER_APPROVAL_FAILED:{exc}")
    ordered = tuple(sorted(reasons))
    body = {"approved": not ordered, "action": action, "family_id": evidence.family_id,
            "evidence_sha256": evidence.evidence_sha256, "scope_sha256": scope,
            "reasons": ordered}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                              allow_nan=False).encode("utf-8")).hexdigest()
    return FamilyActionVerdict(not ordered, action, evidence.family_id,
                               evidence.evidence_sha256, scope, ordered, digest)


# ---- FE-0130 transition log ----

class TransitionLog:
    def __init__(self) -> None:
        self._rows: list[dict] = []

    def record(self, kind: str, subject: str, detail: str, at: datetime) -> int:
        if kind not in ("DOWNGRADE", "DRIFT", "ROLLBACK", "PROMOTION", "HUMAN_EXCEPTION"):
            raise GovernanceError("unknown transition kind")
        self._rows.append({"kind": kind, "subject": subject, "detail": detail, "at": at.isoformat()})
        return len(self._rows)

    def rows(self) -> list[dict]:
        return [dict(r) for r in self._rows]

    def delete(self, index: int) -> None:
        raise GovernanceError("transitions are never deleted")


# ---- FE-0131 safe-policy reversion ----

@dataclass
class SafetyGovernor:
    active_policy: str = "CANDIDATE"
    new_orders_allowed: bool = True
    cancelled_open_orders: int = 0

    def observe(self, event: str, open_orders: int = 0) -> None:
        if event in ("DRIFT", "OUTAGE", "ACCOUNTING_MISMATCH"):
            self.active_policy = "FROZEN_SAFE"
            self.new_orders_allowed = False
            self.cancelled_open_orders += open_orders
