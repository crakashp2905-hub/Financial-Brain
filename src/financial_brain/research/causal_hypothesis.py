"""Training-only candidate hypothesis proposals that cannot change live strategy state."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path

from financial_brain.forward_edge.durable import DurableJournal


class HypothesisError(ValueError):
    """Invalid candidate hypothesis or evidence scope."""


@dataclass(frozen=True)
class HypothesisPlan:
    plan_id: str
    strategy_sha256: str
    development_data_sha256: str
    protocol_sha256: str
    development_start: str
    development_end: str
    protected_validation_start: str
    protected_validation_end: str
    frozen_at_utc: str
    minimum_sessions: int
    expected_direction: str
    minimum_effect_bp: float
    mechanism: str
    falsifier: str

    def __post_init__(self) -> None:
        if not isinstance(self.plan_id, str) or not self.plan_id.strip():
            raise HypothesisError("plan_id is required")
        for name in ("strategy_sha256", "development_data_sha256", "protocol_sha256"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise HypothesisError(f"{name} must be lowercase SHA-256")
        try:
            start, end, validation_start, validation_end = map(date.fromisoformat, (
                self.development_start, self.development_end,
                self.protected_validation_start, self.protected_validation_end,
            ))
            frozen = datetime.fromisoformat(self.frozen_at_utc.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise HypothesisError("plan dates or freeze timestamp are invalid") from exc
        if (start > end or end >= validation_start or validation_start > validation_end
                or frozen.tzinfo is None or frozen.utcoffset() is None or frozen.date() > start):
            raise HypothesisError("development/validation windows and preregistration freeze are invalid")
        if (isinstance(self.minimum_sessions, bool) or not isinstance(self.minimum_sessions, int)
                or self.minimum_sessions < 2):
            raise HypothesisError("minimum_sessions must be at least two")
        if self.expected_direction not in {"POSITIVE", "NEGATIVE"}:
            raise HypothesisError("expected_direction must be POSITIVE or NEGATIVE")
        if (isinstance(self.minimum_effect_bp, bool) or not isinstance(self.minimum_effect_bp, (int, float))
                or not math.isfinite(self.minimum_effect_bp) or self.minimum_effect_bp <= 0):
            raise HypothesisError("minimum_effect_bp must be finite and positive")
        if any(not isinstance(value, str) or not value.strip() for value in (self.mechanism, self.falsifier)):
            raise HypothesisError("mechanism and falsifier must be written before evaluating outcomes")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


def propose_candidate_hypothesis(
    *, plan: HypothesisPlan, pattern_id: str, pattern_description: str,
    observations: Iterable[Mapping[str, object]],
) -> dict:
    """Summarize paired development observations into a proposal, never a strategy edit."""
    if not isinstance(pattern_id, str) or not pattern_id.strip():
        raise HypothesisError("pattern_id is required")
    if not isinstance(pattern_description, str) or not pattern_description.strip():
        raise HypothesisError("pattern_description is required")
    rows, seen_days, seen_ids = [], set(), set()
    for row in observations:
        if not isinstance(row, Mapping) or row.get("split") != "DEVELOPMENT":
            raise HypothesisError("hypothesis evidence must be DEVELOPMENT only")
        try:
            day = date.fromisoformat(row["session_date"]).isoformat()
            evidence_id = row["evidence_id"]
            candidate = row["pattern_net_alpha_bp"]
            control = row["matched_control_net_alpha_bp"]
        except (KeyError, TypeError, ValueError) as exc:
            raise HypothesisError("paired session, evidence ID, and net-alpha metrics are required") from exc
        if not plan.development_start <= day <= plan.development_end or day >= plan.protected_validation_start:
            raise HypothesisError("observation is outside the training window")
        if day in seen_days:
            raise HypothesisError("one matched aggregate observation per session is required")
        if not isinstance(evidence_id, str) or not evidence_id.strip() or evidence_id in seen_ids:
            raise HypothesisError("evidence IDs must be unique and nonempty")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
               for value in (candidate, control)):
            raise HypothesisError("alpha outcomes must be finite numbers")
        seen_ids.add(evidence_id)
        seen_days.add(day)
        rows.append({"split": "DEVELOPMENT", "session_date": day, "evidence_id": evidence_id,
                     "pattern_net_alpha_bp": float(candidate),
                     "matched_control_net_alpha_bp": float(control),
                     "paired_delta_bp": float(candidate) - float(control)})
    if len(seen_days) < plan.minimum_sessions:
        raise HypothesisError("development sample is below the predeclared minimum sessions")
    rows.sort(key=lambda row: (row["session_date"], row["evidence_id"]))
    delta = sum(row["paired_delta_bp"] for row in rows) / len(rows)
    supports = (delta >= plan.minimum_effect_bp if plan.expected_direction == "POSITIVE"
                else delta <= -plan.minimum_effect_bp)
    record = {
        "proposal_id": pattern_id, "plan_id": plan.plan_id, "plan_sha256": plan.sha256,
        "pattern_description": pattern_description, "mechanism": plan.mechanism,
        "candidate_statement": (f"The pattern '{pattern_description}' may change net alpha versus its matched control; "
                                f"proposed direction {plan.expected_direction.lower()} by at least "
                                f"{plan.minimum_effect_bp:g} bp."),
        "prewritten_falsifier": plan.falsifier,
        "expected_direction": plan.expected_direction, "minimum_effect_bp": plan.minimum_effect_bp,
        "development_mean_paired_delta_bp": delta,
        "development_falsifier_triggered": not supports,
        "development_session_count": len(seen_days), "observation_count": len(rows),
        "evidence_ids": [row["evidence_id"] for row in rows], "observations": rows,
        "status": "PROPOSED_PENDING_INDEPENDENT_REVIEW",
        "paper_strategy_mutation": "FORBIDDEN",
        "trial_budget_effect": "UNCHANGED",
        "claim_scope": "Development association only; not a causal result and not registered forward evidence.",
    }
    record["proposal_sha256"] = hashlib.sha256(json.dumps(record, sort_keys=True,
                                                           separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return record


class HypothesisJournal:
    """Append-only proposal archive; it exposes no API to change paper rules or trial budgets."""

    def __init__(self, path: Path) -> None:
        self.journal = DurableJournal(path)

    def record(self, proposal: Mapping[str, object]) -> str:
        if proposal.get("status") != "PROPOSED_PENDING_INDEPENDENT_REVIEW":
            raise HypothesisError("candidate must remain pending independent review")
        if proposal.get("paper_strategy_mutation") != "FORBIDDEN" or proposal.get("trial_budget_effect") != "UNCHANGED":
            raise HypothesisError("hypothesis proposals cannot edit paper rules or budgets")
        body = dict(proposal)
        digest = body.pop("proposal_sha256", None)
        expected = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                          allow_nan=False).encode()).hexdigest()
        # proposal digest is over the record before its digest field was appended.
        if digest != expected:
            raise HypothesisError("proposal digest does not match its contents")
        record = {"t": "CANDIDATE_HYPOTHESIS", **dict(proposal)}
        prior = next((row for row in self.journal.records()
                      if row.get("proposal_id") == proposal.get("proposal_id")), None)
        if prior is not None:
            if prior != record:
                raise HypothesisError("hypothesis proposal identity is immutable")
            return str(digest)
        self.journal.append(record)
        return str(digest)
