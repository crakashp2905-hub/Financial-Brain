"""Human-approved, pre-observation promotion policy and fail-closed evidence gate."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path

from .approvals import Approval, ApprovalError, validate_approval
from .sample_size import SampleSizePlan


class PromotionPolicyError(ValueError):
    pass


def _aware(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PromotionPolicyError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


@dataclass(frozen=True)
class PromotionThresholds:
    policy_id: str
    sample_size_plan: SampleSizePlan
    max_drawdown_fraction: float
    minimum_quote_coverage: float
    minimum_net_alpha_lcb_bps: float
    familywise_alpha: float
    max_regime_block_share: float
    benchmark_id: str
    benchmark_sha256: str
    execution_cost_sha256: str
    cost_multipliers: tuple[float, ...] = (1.0, 1.5, 2.0)
    fill_rates: tuple[float, ...] = (1.0, 0.75, 0.5)

    def __post_init__(self) -> None:
        if not self.policy_id.strip() or not self.benchmark_id.strip():
            raise PromotionPolicyError("policy and benchmark identifiers are required")
        if not isinstance(self.sample_size_plan, SampleSizePlan):
            raise PromotionPolicyError("minimum sessions must be derived from a frozen SampleSizePlan")
        try:
            _aware(datetime.fromisoformat(self.sample_size_plan.first_observation_at_utc.replace("Z", "+00:00")),
                   "first observation")
            _aware(datetime.fromisoformat(self.sample_size_plan.frozen_at_utc.replace("Z", "+00:00")),
                   "sample design freeze")
        except (AttributeError, ValueError) as exc:
            raise PromotionPolicyError("sample design timestamps are invalid") from exc
        if self.sample_size_plan.required_sessions < 2:
            raise PromotionPolicyError("sample design must require at least two independent sessions")
        if self.sample_size_plan.familywise_alpha != self.familywise_alpha:
            raise PromotionPolicyError("promotion familywise alpha must match the frozen sample-size design")
        for name, digest in (("sample design", self.sample_design_sha256),
                             ("benchmark", self.benchmark_sha256),
                             ("execution cost", self.execution_cost_sha256)):
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise PromotionPolicyError(f"{name} evidence must be a SHA-256 digest")
        if not math.isfinite(self.max_drawdown_fraction) or not -1 < self.max_drawdown_fraction < 0:
            raise PromotionPolicyError("maximum drawdown must be a loss fraction between -1 and 0")
        if not math.isfinite(self.minimum_quote_coverage) or not 0 < self.minimum_quote_coverage <= 1:
            raise PromotionPolicyError("minimum quote coverage must be in (0, 1]")
        if not math.isfinite(self.minimum_net_alpha_lcb_bps):
            raise PromotionPolicyError("minimum net-alpha lower bound must be finite")
        if not math.isfinite(self.familywise_alpha) or not 0 < self.familywise_alpha < 1:
            raise PromotionPolicyError("familywise alpha must be in (0, 1)")
        if not math.isfinite(self.max_regime_block_share) or not 0 < self.max_regime_block_share <= 1:
            raise PromotionPolicyError("maximum regime block share must be in (0, 1]")
        if self.cost_multipliers != (1.0, 1.5, 2.0) or self.fill_rates != (1.0, 0.75, 0.5):
            raise PromotionPolicyError("promotion policy must include the complete registered cost/fill stress grid")

    def sha256(self) -> str:
        body = asdict(self)
        return hashlib.sha256(_canonical(body)).hexdigest()

    @property
    def first_observation_at_utc(self) -> datetime:
        return datetime.fromisoformat(self.sample_size_plan.first_observation_at_utc.replace("Z", "+00:00"))

    @property
    def minimum_sessions(self) -> int:
        return self.sample_size_plan.required_sessions

    @property
    def sample_design_sha256(self) -> str:
        return self.sample_size_plan.sha256()


@dataclass(frozen=True)
class FrozenPromotionPolicy:
    thresholds: PromotionThresholds
    frozen_at_utc: datetime
    policy_sha256: str
    approving_actor_id: str


def freeze_policy(thresholds: PromotionThresholds, approval: Approval, *, frozen_at_utc: datetime) -> FrozenPromotionPolicy:
    frozen = _aware(frozen_at_utc, "policy freeze timestamp")
    first = _aware(thresholds.first_observation_at_utc, "first observation")
    if frozen >= first:
        raise PromotionPolicyError("promotion policy must be frozen before confirmatory observations")
    digest = thresholds.sha256()
    try:
        validate_approval(approval, kind="PROMOTION_POLICY", subject=thresholds.policy_id,
                          scope_sha256=digest, now=frozen)
    except ApprovalError as exc:
        raise PromotionPolicyError(f"promotion policy requires a valid scoped human approval: {exc}") from exc
    if _aware(approval.expires_at_utc, "approval expiry") < first:
        raise PromotionPolicyError("promotion policy approval expires before the first observation")
    return FrozenPromotionPolicy(thresholds, frozen, digest, approval.actor_id)


REQUIRED_EVIDENCE = (
    "eligible_session_dates", "verified_session_dates", "observed_max_drawdown_fraction", "quote_coverage",
    "net_alpha_lcb_bps", "risk_matched_benchmark_alpha_lcb_bps", "cost_fill_net_alpha_bps",
    "adverse_selection_net_alpha_bps", "multiplicity_adjusted",
    "incidents_closed", "ledger_reconciled", "data_rights_verified", "custody_verified",
    "benchmark_sha256", "execution_cost_sha256", "sample_design_sha256",
    "incident_review_sha256", "ledger_reconciliation_sha256", "data_rights_evidence_sha256",
    "custody_receipt_sha256",
)


def evaluate_promotion(policy: FrozenPromotionPolicy, evidence: dict) -> dict:
    """Evaluate a complete evidence row; absent fields never default to a pass."""
    missing = sorted(set(REQUIRED_EVIDENCE) - set(evidence))
    reasons = [f"MISSING:{name}" for name in missing]
    if missing:
        return {"verdict": "INSUFFICIENT_EVIDENCE", "reasons": reasons,
                "policy_sha256": policy.policy_sha256, "promotable": False}
    threshold = policy.thresholds
    def session_dates(field: str) -> set[date] | None:
        values = evidence[field]
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            reasons.append(f"INVALID:{field}")
            return None
        try:
            parsed = [date.fromisoformat(value) for value in values]
        except ValueError:
            reasons.append(f"INVALID:{field}")
            return None
        if len(parsed) != len(set(parsed)):
            reasons.append(f"DUPLICATE:{field}")
            return None
        return set(parsed)

    eligible_dates = session_dates("eligible_session_dates")
    verified_dates = session_dates("verified_session_dates")
    first_session = threshold.first_observation_at_utc.date()
    if eligible_dates is not None and any(day < first_session for day in eligible_dates):
        reasons.append("SESSION_PRECEDES_FIRST_OBSERVATION")
    if eligible_dates is not None and verified_dates is not None:
        if verified_dates != eligible_dates:
            reasons.append("INDEPENDENT_SESSION_RECEIPTS_INCOMPLETE")
        if len(eligible_dates) < threshold.minimum_sessions:
            reasons.append("INSUFFICIENT_INDEPENDENT_SESSIONS")
    numeric_limits = (
        ("observed_max_drawdown_fraction", threshold.max_drawdown_fraction, "le", "DRAWDOWN_LIMIT_BREACHED"),
        ("quote_coverage", threshold.minimum_quote_coverage, "lt", "QUOTE_COVERAGE_BELOW_POLICY"),
        ("net_alpha_lcb_bps", threshold.minimum_net_alpha_lcb_bps, "le", "NET_ALPHA_LOWER_BOUND_BELOW_POLICY"),
        ("risk_matched_benchmark_alpha_lcb_bps", 0.0, "le", "RISK_MATCHED_BENCHMARK_ALPHA_NOT_POSITIVE"),
    )
    for field, limit, operator, failure in numeric_limits:
        value = evidence[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            reasons.append(f"INVALID:{field}")
        elif ((operator == "le" and value <= limit) or (operator == "lt" and value < limit)):
            reasons.append(failure)
    required_scenarios = {
        f"{cost:.1f}x@{fill:.2f}" for cost in threshold.cost_multipliers for fill in threshold.fill_rates
    }
    grid = evidence["cost_fill_net_alpha_bps"]
    if not isinstance(grid, dict) or set(grid) != required_scenarios:
        reasons.append("COST_FILL_STRESS_GRID_INCOMPLETE")
    else:
        grid_values = list(grid.values())
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
               for value in grid_values):
            reasons.append("COST_FILL_STRESS_GRID_INVALID")
        elif any(value <= 0 for value in grid_values):
            reasons.append("COST_FILL_STRESS_NOT_POSITIVE")
    adverse = evidence["adverse_selection_net_alpha_bps"]
    if isinstance(adverse, bool) or not isinstance(adverse, (int, float)) or not math.isfinite(adverse):
        reasons.append("INVALID:adverse_selection_net_alpha_bps")
    elif adverse <= 0:
        reasons.append("ADVERSE_SELECTION_STRESS_NOT_POSITIVE")
    if evidence["multiplicity_adjusted"] is not True:
        reasons.append("MULTIPLICITY_NOT_ADJUSTED")
    for field in ("incidents_closed", "ledger_reconciled", "data_rights_verified", "custody_verified"):
        if evidence[field] is not True:
            reasons.append(field.upper() + "_NOT_CONFIRMED")
    for field in ("benchmark_sha256", "execution_cost_sha256", "sample_design_sha256"):
        if evidence[field] != getattr(threshold, field):
            reasons.append(field.upper() + "_MISMATCH")
    for field in ("incident_review_sha256", "ledger_reconciliation_sha256",
                  "data_rights_evidence_sha256", "custody_receipt_sha256"):
        digest = evidence[field]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            reasons.append("INVALID:" + field)
    return {"verdict": "PROMOTION_GRADE" if not reasons else "INSUFFICIENT_EVIDENCE",
            "reasons": reasons, "policy_sha256": policy.policy_sha256, "promotable": not reasons}


class PromotionDecisionLedger:
    """Append-only record of frozen-cohort promotion decisions; never authorizes physical orders."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self.records()

    def records(self) -> list[dict]:
        previous = "GENESIS"
        output = []
        for sequence, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                row = json.loads(line)
                record = row["record"]
                digest = hashlib.sha256((previous + _canonical_text(record)).encode("utf-8")).hexdigest()
                if row["seq"] != sequence or row["prev"] != previous or row["hash"] != digest:
                    raise PromotionPolicyError(f"promotion decision ledger record {sequence} is corrupt")
                previous = digest
                output.append(record)
            except PromotionPolicyError:
                raise
            except (ValueError, KeyError, TypeError) as exc:
                raise PromotionPolicyError(f"promotion decision ledger record {sequence} is invalid") from exc
        return output

    @property
    def head(self) -> str:
        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return rows[-1]["hash"] if rows else "GENESIS"

    def record(self, *, decision_id: str, cohort_id: str, policy: FrozenPromotionPolicy,
               evidence: dict, decided_at_utc: datetime) -> dict:
        if not decision_id.strip() or not cohort_id.strip():
            raise PromotionPolicyError("decision and frozen cohort identifiers are required")
        decided = _aware(decided_at_utc, "decision timestamp")
        frozen = _aware(policy.frozen_at_utc, "policy freeze timestamp")
        if decided < frozen:
            raise PromotionPolicyError("decision timestamp precedes policy freeze")
        verdict = evaluate_promotion(policy, evidence)
        body = {"decision_id": decision_id, "cohort_id": cohort_id,
                "policy_sha256": policy.policy_sha256, "evidence_sha256": hashlib.sha256(_canonical(evidence)).hexdigest(),
                "verdict": verdict["verdict"], "reasons": verdict["reasons"],
                "decided_at_utc": decided.isoformat(), "physical_orders": False,
                "live_cash_permission": False}
        existing = next((item for item in self.records() if item["decision_id"] == decision_id), None)
        if existing is not None:
            if existing != body:
                raise PromotionPolicyError("promotion decision id is immutable and already has different evidence")
            return existing
        previous = self.head
        sequence = len(self.records()) + 1
        digest = hashlib.sha256((previous + _canonical_text(body)).encode("utf-8")).hexdigest()
        with self.path.open("ab") as stream:
            stream.write((_canonical_text({"seq": sequence, "prev": previous,
                                          "hash": digest, "record": body}) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        return body


def _canonical_text(value: object) -> str:
    return _canonical(value).decode("utf-8")
