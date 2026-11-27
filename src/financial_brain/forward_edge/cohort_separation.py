"""Validate distinct SHADOW and LIVE_PAPER books and preserve evidence tiering."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class CohortSeparationError(ValueError):
    """Cohort pair aliases capital, policy, execution or evidence identities."""


@dataclass(frozen=True, slots=True)
class CohortBook:
    cohort_id: str
    mode: str
    policy_sha256: str
    execution_asset_id: str
    ledger_id: str
    capital_inr: Decimal

    def __post_init__(self) -> None:
        for value, label in ((self.cohort_id, "cohort_id"), (self.mode, "mode"),
                             (self.execution_asset_id, "execution_asset_id"),
                             (self.ledger_id, "ledger_id")):
            if not isinstance(value, str) or not value.strip():
                raise CohortSeparationError(f"{label} is required")
        if len(self.policy_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.policy_sha256):
            raise CohortSeparationError("policy hash must be lowercase SHA-256")
        try:
            capital = Decimal(self.capital_inr)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise CohortSeparationError("capital must be a finite non-negative INR amount") from exc
        if not capital.is_finite() or capital < 0:
            raise CohortSeparationError("capital must be a finite non-negative INR amount")
        object.__setattr__(self, "capital_inr", capital)


@dataclass(frozen=True, slots=True)
class CohortPair:
    shadow: CohortBook
    live_paper: CohortBook

    def __post_init__(self) -> None:
        left, right = self.shadow, self.live_paper
        if left.mode != "SHADOW" or right.mode != "LIVE_PAPER":
            raise CohortSeparationError("pair requires SHADOW and LIVE_PAPER modes")
        if right.capital_inr <= 0:
            raise CohortSeparationError("LIVE_PAPER capital allocation must be positive")
        checks = ((left.cohort_id, right.cohort_id, "cohort IDs"),
                  (left.policy_sha256, right.policy_sha256, "policy hashes"),
                  (left.execution_asset_id, right.execution_asset_id, "execution assets"),
                  (left.ledger_id, right.ledger_id, "paper ledgers"),
                  (left.capital_inr, right.capital_inr, "capital allocations"))
        for first, second, label in checks:
            if first == second:
                raise CohortSeparationError(f"SHADOW and LIVE_PAPER must use distinct {label}")

    def digest(self) -> str:
        body = {
            "schema": "fb.separate_paper_cohorts.v1",
            "shadow": _book_body(self.shadow),
            "live_paper": _book_body(self.live_paper),
            "shadow_emits_fills": False,
            "live_paper_emits_simulated_fills": True,
            "shadow_is_prospective_execution_evidence": False,
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                       allow_nan=False).encode()).hexdigest()

    def authorize_evidence_claim(self, *, source_mode: str, claim: str) -> bool:
        """Prevent shadow logs or historical replay from being relabeled as paper fills."""
        if claim == "PROSPECTIVE_EXECUTION" and source_mode != "LIVE_PAPER":
            return False
        if claim == "OBSERVED_FILL" and source_mode in {"SHADOW", "RESEARCH_REPLAY"}:
            return False
        if source_mode not in {"SHADOW", "LIVE_PAPER", "RESEARCH_REPLAY"}:
            return False
        return True


def _book_body(book: CohortBook) -> dict[str, str]:
    return {"cohort_id": book.cohort_id, "mode": book.mode,
            "policy_sha256": book.policy_sha256,
            "execution_asset_id": book.execution_asset_id, "ledger_id": book.ledger_id,
            "capital_inr": str(book.capital_inr)}
