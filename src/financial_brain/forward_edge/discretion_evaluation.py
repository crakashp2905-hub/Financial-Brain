"""Paired evaluation of human approval/discretion against a frozen model default."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass


class DiscretionEvaluationError(ValueError):
    """Human/model comparison does not share frozen inputs or cost evidence."""


_SHA = re.compile(r"^[0-9a-f]{64}$")
_ACTIONS = {"APPROVE", "REJECT", "WATCH", "NO_TRADE"}


@dataclass(frozen=True)
class DecisionPairOutcome:
    candidate_id: str
    model_information_sha256: str
    owner_information_sha256: str
    model_market_outcome_sha256: str
    owner_market_outcome_sha256: str
    model_cost_schedule_sha256: str
    owner_cost_schedule_sha256: str
    model_evaluation_horizon: str
    owner_evaluation_horizon: str
    model_default_action: str
    owner_action: str
    model_capital_budget_inr: float
    owner_capital_budget_inr: float
    model_net_pnl_inr: float
    owner_net_pnl_inr: float
    costs_complete: bool

    def validate(self) -> None:
        if (not self.candidate_id.strip() or not self.model_evaluation_horizon.strip()
                or not self.owner_evaluation_horizon.strip()):
            raise DiscretionEvaluationError("candidate identity and evaluation horizon are required")
        for name in ("model_information_sha256", "owner_information_sha256",
                     "model_market_outcome_sha256", "owner_market_outcome_sha256",
                     "model_cost_schedule_sha256", "owner_cost_schedule_sha256"):
            if not _SHA.fullmatch(getattr(self, name)):
                raise DiscretionEvaluationError(f"{name} must be a lowercase SHA-256 digest")
        if self.model_default_action not in _ACTIONS or self.owner_action not in _ACTIONS:
            raise DiscretionEvaluationError("model and owner actions must be explicit supported actions")
        if (not math.isfinite(self.model_capital_budget_inr) or self.model_capital_budget_inr <= 0
                or not math.isfinite(self.owner_capital_budget_inr) or self.owner_capital_budget_inr <= 0
                or not math.isfinite(self.model_net_pnl_inr)
                or not math.isfinite(self.owner_net_pnl_inr)):
            raise DiscretionEvaluationError("budget and net outcomes must be finite; budget must be positive")
        if not self.costs_complete:
            raise DiscretionEvaluationError("both outcomes require complete after-cost P&L")


def evaluate_owner_discretion(*, pairs: tuple[DecisionPairOutcome, ...]) -> dict[str, object]:
    """Compute paired owner-vs-model outcome only on same-evidence comparable cases."""
    if not pairs or len({row.candidate_id for row in pairs}) != len(pairs):
        raise DiscretionEvaluationError("unique candidate decision pairs are required")
    for row in pairs:
        row.validate()
    comparable = [row for row in pairs if row.model_information_sha256 == row.owner_information_sha256
                  and row.model_market_outcome_sha256 == row.owner_market_outcome_sha256
                  and row.model_cost_schedule_sha256 == row.owner_cost_schedule_sha256
                  and row.model_capital_budget_inr == row.owner_capital_budget_inr
                  and row.model_evaluation_horizon == row.owner_evaluation_horizon]
    overrides = [row for row in comparable if row.owner_action != row.model_default_action]
    deltas = [(row.owner_net_pnl_inr - row.model_net_pnl_inr) / row.model_capital_budget_inr
              for row in overrides]
    comparable_ids = {row.candidate_id for row in comparable}
    return_rows = []
    for row in pairs:
        matches = row.candidate_id in comparable_ids
        reasons = []
        for field, left, right in (
            ("POINT_IN_TIME_INFORMATION_MISMATCH", row.model_information_sha256, row.owner_information_sha256),
            ("MARKET_OUTCOME_MISMATCH", row.model_market_outcome_sha256, row.owner_market_outcome_sha256),
            ("COST_SCHEDULE_MISMATCH", row.model_cost_schedule_sha256, row.owner_cost_schedule_sha256),
            ("CAPITAL_BUDGET_MISMATCH", row.model_capital_budget_inr, row.owner_capital_budget_inr),
            ("EVALUATION_HORIZON_MISMATCH", row.model_evaluation_horizon, row.owner_evaluation_horizon),
        ):
            if left != right:
                reasons.append(field)
        return_rows.append({"candidate_id": row.candidate_id,
                            "model_default_action": row.model_default_action,
                            "owner_action": row.owner_action,
                            "owner_overrode_model": row.owner_action != row.model_default_action,
                            "comparable": matches,
                            "unmatched_reasons": reasons,
                            "model_net_pnl_inr": row.model_net_pnl_inr if matches else None,
                            "owner_net_pnl_inr": row.owner_net_pnl_inr if matches else None,
                            "owner_minus_model_net_return": (
                                (row.owner_net_pnl_inr - row.model_net_pnl_inr)
                                / row.model_capital_budget_inr if matches else None),
                            "capital_budget_inr": row.model_capital_budget_inr,
                            "information_sha256": row.model_information_sha256,
                            "market_outcome_sha256": row.model_market_outcome_sha256,
                            "cost_schedule_sha256": row.model_cost_schedule_sha256,
                            "evaluation_horizon": row.model_evaluation_horizon,
                            "evidence_class": "PAIRED_CALLER_SUPPLIED_UNVERIFIED" if matches else "UNMATCHED"})
    body: dict[str, object] = {
        "candidate_pairs": len(pairs),
        "comparable_pairs": len(comparable),
        "unresolved_or_unmatched_pairs": len(pairs) - len(comparable),
        "owner_override_count": len(overrides),
        "owner_override_rate": len(overrides) / len(comparable) if comparable else None,
        "mean_owner_minus_model_net_return_on_overrides": (
            sum(deltas) / len(deltas) if deltas else None),
        "owner_override_positive_count": sum(value > 0 for value in deltas),
        "owner_override_negative_count": sum(value < 0 for value in deltas),
        "paired_results": return_rows,
        "causal_or_independent_claim": False,
        "limitations": ["same-information and outcome hashes are caller asserted, not independently verified",
                        "counterfactual outcome validity must be established by the source evidence pipeline"],
    }
    body["evidence_sha256"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()
    return body
