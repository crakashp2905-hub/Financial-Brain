"""Hashable paper execution mandate with separately scoped human capital approval.

The charter fixes the proposed capital/risk envelope, execution mode and instrument
scope. Its digest excludes the approval receipt so a human can approve that exact
digest; a receipt never changes the charter and can never enable physical orders.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from .approvals import Approval, ApprovalError, validate_approval
from .capital_proposal import CapitalRiskProposal


class ExecutionCharterError(ValueError):
    """Execution charter is invalid or lacks a matching human approval."""


@dataclass(frozen=True)
class ExecutionCharter:
    charter_id: str
    owner_id: str
    execution_mode: str
    permitted_instruments: tuple[str, ...]
    capital_risk: CapitalRiskProposal

    def __post_init__(self) -> None:
        if not self.charter_id.strip() or not self.owner_id.strip():
            raise ExecutionCharterError("charter id and named owner are required")
        if self.execution_mode not in {"PAPER", "SHADOW"}:
            raise ExecutionCharterError("only PAPER and SHADOW modes are permitted")
        if (not self.permitted_instruments
                or tuple(sorted(set(self.permitted_instruments))) != self.permitted_instruments):
            raise ExecutionCharterError("permitted instruments must be non-empty, unique and sorted")
        if any(not item.strip() for item in self.permitted_instruments):
            raise ExecutionCharterError("instrument identifiers cannot be blank")
        if self.capital_risk.physical_ordering_enabled:
            raise ExecutionCharterError("physical ordering is disabled by build policy")
        if self.capital_risk.review_owner != self.owner_id:
            raise ExecutionCharterError("capital proposal owner must match the charter owner")

    def body(self) -> dict[str, object]:
        return {
            "schema": "fb.execution_charter.v1",
            "charter_id": self.charter_id,
            "owner_id": self.owner_id,
            "execution_mode": self.execution_mode,
            "permitted_instruments": list(self.permitted_instruments),
            "capital_risk": self.capital_risk.body(),
            "capital_risk_sha256": self.capital_risk.digest(),
            "physical_ordering_enabled": False,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.body(), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def require_human_approval(self, approval: Approval | None, *, now: datetime) -> None:
        if approval is None:
            raise ExecutionCharterError("an exact-scope, expiring human CAPITAL approval is required")
        try:
            validate_approval(approval, kind="CAPITAL", subject=self.charter_id,
                              scope_sha256=self.sha256(), now=now)
        except ApprovalError as exc:
            raise ExecutionCharterError(str(exc)) from exc

