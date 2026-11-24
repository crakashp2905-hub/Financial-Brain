"""Deterministic, fixture-only research campaign contracts.

This is a control-plane rehearsal, not a sandbox for executing generated code. Candidate
text and evaluator metrics are treated as untrusted; this module has no broker, network,
filesystem, or subprocess capability. Production isolation and independent evaluation
remain separate deployment requirements.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


ALLOWED_ACTIONS = frozenset({"PROPOSE_DSL", "WRITE_SCRATCH_CODE", "RUN_FIXTURE_BACKTEST", "REQUEST_HUMAN_REVIEW"})
DENIED_ACTIONS = frozenset({"PLACE_ORDER", "READ_SECRET", "READ_PROTECTED_HOLDOUT", "CHANGE_ENTITLEMENT"})
TERMINAL = frozenset({"NO_EDGE_FOUND", "REJECTED", "REVIEW_REQUESTED"})


@dataclass(frozen=True)
class Candidate:
    hypothesis_id: str
    parent_hypothesis_id: str
    family_id: str
    title: str
    proposal: str
    data_availability: str
    mechanism: str
    distinction_from_failures: str
    evaluation_contract: dict[str, Any]

    def validate(self) -> None:
        for name in ("hypothesis_id", "parent_hypothesis_id", "family_id", "title", "proposal",
                     "data_availability", "mechanism", "distinction_from_failures"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if self.hypothesis_id == self.parent_hypothesis_id:
            raise ValueError("candidate must be a new descendant hypothesis")
        if not isinstance(self.evaluation_contract, dict) or not self.evaluation_contract:
            raise ValueError("sealed evaluation contract is required")
        _canonical(self.evaluation_contract)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def seal_contract(contract: dict[str, Any]) -> str:
    """Seal the candidate's declared evaluation protocol before outcome access."""
    if not isinstance(contract, dict) or not contract:
        raise ValueError("evaluation contract must be a non-empty object")
    return hashlib.sha256(_canonical(contract).encode()).hexdigest()


def critique(candidate: Candidate) -> list[dict[str, str]]:
    """Return explicit review findings; findings are questions, not claims of safety."""
    candidate.validate()
    fields = {
        "lookahead": ("timestamp", "availability", "point-in-time"),
        "costs": ("cost", "fee", "slippage"),
        "multiplicity": ("trial", "family", "multiple"),
        "survivorship": ("survivorship", "universe", "delisted"),
        "capacity": ("capacity", "liquidity", "impact"),
        "novelty": ("different", "prior", "failed", "baseline"),
    }
    text = " ".join((candidate.proposal, candidate.data_availability, candidate.mechanism,
                     candidate.distinction_from_failures)).lower()
    return [{"check": label, "status": "EVIDENCE_MISSING",
             "reason": f"candidate text does not cite {label} controls"}
            for label, words in fields.items() if not any(word in text for word in words)]


@dataclass
class Campaign:
    """In-memory append-only trace for deterministic, fixture-only campaign rehearsal."""
    allowed_context: dict[str, tuple[str, ...]] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    _last_hash: str = "0" * 64

    def _record(self, kind: str, payload: dict[str, Any]) -> None:
        # Store a canonical JSON snapshot so later caller mutations cannot rewrite history.
        payload = json.loads(_canonical(payload))
        body = {"sequence": len(self.events), "kind": kind, "payload": payload,
                "previous_hash": self._last_hash}
        digest = hashlib.sha256(_canonical(body).encode()).hexdigest()
        self.events.append({**body, "sha256": digest})
        self._last_hash = digest

    def read_context(self, key: str) -> tuple[str, ...]:
        """Expose only caller-supplied identifiers from the declared read-only catalog."""
        if key not in self.allowed_context:
            raise PermissionError("context is outside the declared research catalog")
        result = tuple(self.allowed_context[key])
        self._record("CONTEXT_READ", {"catalog": key, "items": list(result)})
        return result

    def authorize(self, action: str) -> bool:
        if action in DENIED_ACTIONS or action not in ALLOWED_ACTIONS:
            self._record("ACTION_DENIED", {"action": action})
            return False
        self._record("ACTION_ALLOWED", {"action": action})
        return True

    def run_fixture(self, candidate: Candidate, fixture: dict[str, Any]) -> dict[str, Any]:
        """Validate a sealed fixture record without running candidate-provided code."""
        candidate.validate()
        if not self.authorize("RUN_FIXTURE_BACKTEST"):
            raise PermissionError("fixture backtest action denied")
        digest = seal_contract(candidate.evaluation_contract)
        if fixture.get("contract_sha256") != digest:
            raise ValueError("fixture does not match the sealed evaluation contract")
        # Metrics are only read after the contract digest has been checked.
        metrics = fixture.get("metrics")
        if not isinstance(metrics, dict):
            raise ValueError("fixture metrics must be an object")
        for key, value in metrics.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"metric {key} must be numeric")
        outcome = str(fixture.get("outcome", "REJECTED"))
        if outcome not in TERMINAL:
            raise ValueError("fixture outcome must be a terminal research state")
        result = {"hypothesis_id": candidate.hypothesis_id, "family_id": candidate.family_id,
                  "parent_hypothesis_id": candidate.parent_hypothesis_id,
                  "contract_sha256": digest, "metrics": dict(metrics), "outcome": outcome,
                  "execution_allowed": False}
        self._record("FIXTURE_EVALUATED", result)
        return result

    def rehearse(self, candidate: Candidate, fixture: dict[str, Any]) -> dict[str, Any]:
        candidate.validate()
        self._record("CANDIDATE_PROPOSED", asdict(candidate))
        findings = critique(candidate)
        self._record("INDEPENDENT_CRITIQUE", {"hypothesis_id": candidate.hypothesis_id,
                                              "findings": findings})
        result = self.run_fixture(candidate, fixture)
        result["critique"] = findings
        result["trace_sha256"] = self._last_hash
        return result

    def verify_trace(self) -> bool:
        previous = "0" * 64
        for sequence, event in enumerate(self.events):
            body = {"sequence": sequence, "kind": event["kind"], "payload": event["payload"],
                    "previous_hash": previous}
            digest = hashlib.sha256(_canonical(body).encode()).hexdigest()
            if event.get("sha256") != digest:
                return False
            previous = digest
        return previous == self._last_hash
