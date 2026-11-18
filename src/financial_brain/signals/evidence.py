"""Five evidence tiers, signal decision record, sleeves and counterfactuals
(FB-0003, FB-0011, FB-0402, FB-0404, FB-0409, FB-0410, FB-0411, FB-0412).

* ``Tier``: FIXTURE < REPLAY < LOCAL_PAPER < ATTESTED_FORWARD < BROKER_APPROVED. Replayed or
  historical results can never be labelled forward; ``promote`` checks the evidence each tier needs.
* ``DecisionRecord``: decision-time features, expected distribution with interval, benchmark, costs,
  risk/return and contraindications, plus holding window; missing inputs are explicit.
* ``Sleeve``: AUTONOMOUS keeps objective outcomes; MANUAL_APPROVED is a separate sleeve.
* ``Counterfactual``: rejected signals tracked forward, never added to portfolio P&L.
* ``llm_may_order`` is always False: model output feeds read-only signal generation only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum


class EvidenceError(ValueError):
    pass


class Tier(IntEnum):
    FIXTURE = 0
    REPLAY = 1
    LOCAL_PAPER = 2
    ATTESTED_FORWARD = 3
    BROKER_APPROVED = 4


REQUIRES = {
    Tier.REPLAY: {"recorded_source_hash"},
    Tier.LOCAL_PAPER: {"recorded_source_hash", "decided_before_outcome"},
    Tier.ATTESTED_FORWARD: {"recorded_source_hash", "decided_before_outcome", "independent_attestation"},
    Tier.BROKER_APPROVED: {"recorded_source_hash", "decided_before_outcome", "independent_attestation",
                           "owner_broker_compliance_approval"},
}


def promote(current: Tier, target: Tier, evidence: set[str], *, is_replay: bool) -> Tier:
    if target <= current:
        return current
    if is_replay and target >= Tier.LOCAL_PAPER:
        raise EvidenceError("replayed or historical results are never forward evidence")
    missing = REQUIRES[target] - evidence
    if missing:
        raise EvidenceError(f"missing evidence for {target.name}: {sorted(missing)}")
    return target


@dataclass(frozen=True)
class DecisionRecord:
    signal_id: str
    decided_at: datetime
    features: tuple[tuple[str, float | None], ...]
    expected_return: float | None
    interval: tuple[float, float] | None
    benchmark: str
    cost_estimate: float | None
    risk_estimate: float | None
    contraindications: tuple[str, ...]
    holding_window: timedelta
    missing: tuple[str, ...] = field(default=())

    @classmethod
    def build(cls, signal_id: str, decided_at: datetime, features: dict[str, float | None], *,
              expected_return: float | None, interval: tuple[float, float] | None, benchmark: str,
              cost_estimate: float | None, risk_estimate: float | None, contraindications: list[str],
              holding_window: timedelta) -> DecisionRecord:
        if interval is not None and expected_return is not None and not interval[0] <= expected_return <= interval[1]:
            raise EvidenceError("expected return outside its own interval")
        if holding_window <= timedelta(0):
            raise EvidenceError("holding window must be positive")
        missing = [k for k, v in features.items() if v is None]
        missing += [k for k, v in (("expected_return", expected_return), ("cost_estimate", cost_estimate),
                                   ("risk_estimate", risk_estimate)) if v is None]
        return cls(signal_id, decided_at, tuple(sorted(features.items())), expected_return, interval,
                   benchmark, cost_estimate, risk_estimate, tuple(contraindications), holding_window,
                   tuple(missing))

    @property
    def complete(self) -> bool:
        return not self.missing


class Sleeve:
    AUTONOMOUS = "AUTONOMOUS"
    MANUAL_APPROVED = "MANUAL_APPROVED"


@dataclass
class Counterfactual:
    signal_id: str
    rejected_at: datetime
    reason: str
    observations: list[tuple[datetime, float]] = field(default_factory=list)
    counts_in_portfolio: bool = False

    def observe(self, at: datetime, hypothetical_return: float) -> None:
        if at <= self.rejected_at:
            raise EvidenceError("counterfactual observations must follow the rejection")
        self.observations.append((at, hypothetical_return))


def llm_may_order() -> bool:
    """Language-model output never sends orders (FB-0409)."""
    return False
