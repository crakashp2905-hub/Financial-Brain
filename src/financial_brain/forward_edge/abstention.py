"""Frozen abstention-policy evaluation with missed-winner accounting (FE-0084)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone


class AbstentionError(ValueError):
    """Invalid frozen policy or incomplete abstention evaluation."""


@dataclass(frozen=True)
class FrozenAbstentionPolicy:
    policy_id: str
    strategy_version_hash: str
    parameters_hash: str
    frozen_at: datetime
    first_eligible_session: date

    def __post_init__(self) -> None:
        if not all(x.strip() for x in (self.policy_id, self.strategy_version_hash, self.parameters_hash)):
            raise AbstentionError("policy identity and hashes are required")
        if self.frozen_at.tzinfo is None or self.frozen_at.utcoffset() is None:
            raise AbstentionError("policy freeze timestamp must be timezone aware")
        if self.frozen_at.date() >= self.first_eligible_session:
            raise AbstentionError("policy must be frozen before the cohort's first eligible session")

    def digest(self) -> str:
        body = {"policy_id": self.policy_id, "strategy_version_hash": self.strategy_version_hash,
                "parameters_hash": self.parameters_hash,
                "frozen_at": self.frozen_at.astimezone(timezone.utc).isoformat(),
                "first_eligible_session": self.first_eligible_session.isoformat()}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class AbstentionOutcome:
    opportunity_id: str
    session: date
    policy_id: str
    policy_hash: str
    action: str
    outcome_status: str
    policy_realized_net_return: float | None
    counterfactual_net_return: float | None
    abstain_reason: str | None = None

    def __post_init__(self) -> None:
        if not all(x.strip() for x in (self.opportunity_id, self.policy_id, self.policy_hash)):
            raise AbstentionError("opportunity and policy lineage are required")
        if self.action not in {"TRADE", "ABSTAIN"} or self.outcome_status not in {"RESOLVED", "UNRESOLVED"}:
            raise AbstentionError("invalid policy action or outcome status")
        for name, value in (("policy_realized_net_return", self.policy_realized_net_return),
                            ("counterfactual_net_return", self.counterfactual_net_return)):
            if value is not None and not math.isfinite(float(value)):
                raise AbstentionError(f"{name} must be finite when supplied")
        if self.outcome_status == "RESOLVED":
            if self.policy_realized_net_return is None or self.counterfactual_net_return is None:
                raise AbstentionError("resolved outcomes require realized and counterfactual net returns")
            if self.action == "ABSTAIN" and self.policy_realized_net_return != 0:
                raise AbstentionError("abstention cannot realize a trade return")
        elif self.policy_realized_net_return is not None or self.counterfactual_net_return is not None:
            raise AbstentionError("unresolved outcomes cannot carry invented returns")
        if self.action == "ABSTAIN" and not (self.abstain_reason and self.abstain_reason.strip()):
            raise AbstentionError("abstention reason is required")


@dataclass(frozen=True)
class AbstentionReport:
    policy_id: str
    policy_hash: str
    total_opportunities: int
    traded: int
    abstained: int
    resolved: int
    unresolved: int
    realized_policy_net_return_sum: float
    missed_winners: int
    abstention_avoided_losses: int
    unresolved_abstentions: int
    report_hash: str


def evaluate_abstentions(policy: FrozenAbstentionPolicy, expected_opportunity_ids: tuple[str, ...],
                         outcomes: tuple[AbstentionOutcome, ...]) -> AbstentionReport:
    """Evaluate every declared opportunity; unresolved outcomes stay in the denominator."""
    if len(set(expected_opportunity_ids)) != len(expected_opportunity_ids) or not expected_opportunity_ids:
        raise AbstentionError("expected opportunity IDs must be explicit, non-empty and unique")
    if not outcomes:
        raise AbstentionError("all expected opportunities need a decision/outcome row")
    by_id = {item.opportunity_id: item for item in outcomes}
    if len(by_id) != len(outcomes) or set(by_id) != set(expected_opportunity_ids):
        raise AbstentionError("outcome IDs must exactly match the frozen opportunity universe")
    policy_hash = policy.digest()
    for item in outcomes:
        if item.policy_id != policy.policy_id or item.policy_hash != policy_hash:
            raise AbstentionError("evaluation policy lineage differs from the frozen policy")
        if item.session < policy.first_eligible_session:
            raise AbstentionError("outcome predates the policy's first eligible session")
    ordered = [by_id[key] for key in sorted(by_id)]
    traded = sum(item.action == "TRADE" for item in ordered)
    abstained = len(ordered) - traded
    resolved = [item for item in ordered if item.outcome_status == "RESOLVED"]
    unresolved = len(ordered) - len(resolved)
    realized = sum(float(item.policy_realized_net_return or 0.0) for item in resolved)
    missed = sum(item.action == "ABSTAIN" and float(item.counterfactual_net_return or 0.0) > 0
                 for item in resolved)
    avoided = sum(item.action == "ABSTAIN" and float(item.counterfactual_net_return or 0.0) < 0
                  for item in resolved)
    unresolved_abstentions = sum(item.action == "ABSTAIN" and item.outcome_status == "UNRESOLVED"
                                 for item in ordered)
    fields = {"policy_id": policy.policy_id, "policy_hash": policy_hash,
              "total_opportunities": len(ordered), "traded": traded, "abstained": abstained,
              "resolved": len(resolved), "unresolved": unresolved,
              "realized_policy_net_return_sum": realized, "missed_winners": missed,
              "abstention_avoided_losses": avoided, "unresolved_abstentions": unresolved_abstentions}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return AbstentionReport(policy.policy_id, policy_hash, len(ordered), traded, abstained,
                            len(resolved), unresolved, realized, missed, avoided,
                            unresolved_abstentions, digest)
