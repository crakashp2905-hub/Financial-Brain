"""Shadow learning registry, labels, calibration, drift and promotion guard (stage 18).

Rules enforced in code, not documentation:
- A label exists only after its horizon has matured (CP-0282).
- A feature must be knowable at the decision time (CP-0283).
- Versions are immutable; rollback moves a pointer and keeps every artifact (CP-0289, CP-0295).
- A corrupted artifact fails closed (CP-0333).
- Calibration needs enough valid labels, else ``NO_CALIBRATION`` (CP-0291).
- A learned score can never override a deterministic rejection (CP-0293).
- Promotion needs forward or externally confirmed evidence; synthetic or historical-only
  results are refused (CP-0297, CP-0400).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from financial_brain.patterns.spec import EvidenceTier


class ImmatureLabel(ValueError):
    """A training label whose outcome window has not yet closed."""


class FutureFeature(ValueError):
    """A feature that was not knowable at the decision time."""


class PromotionRefused(RuntimeError):
    """Promotion without the required evidence tier."""


@dataclass(frozen=True)
class Label:
    event_id: str
    decision_index: int
    horizon_end_index: int
    outcome: float


def make_label(event_id: str, decision_index: int, horizon_end_index: int, outcome: float, *, now_index: int) -> Label:
    if now_index < horizon_end_index:
        raise ImmatureLabel(f"{event_id}: horizon ends at {horizon_end_index}, now is {now_index}")
    return Label(event_id, decision_index, horizon_end_index, outcome)


@dataclass(frozen=True)
class Feature:
    name: str
    value: float
    knowable_at: int


def assemble(features: list[Feature], *, decision_index: int) -> dict[str, float]:
    """Refuse any feature that was not knowable at the decision (CP-0283)."""
    row: dict[str, float] = {}
    for f in features:
        if f.knowable_at > decision_index:
            raise FutureFeature(f"{f.name} knowable at {f.knowable_at} > decision {decision_index}")
        row[f.name] = f.value
    return dict(sorted(row.items()))


@dataclass(frozen=True)
class ModelVersion:
    version_id: str
    artifact_sha256: str
    training_hash: str
    features: tuple[str, ...]
    evaluation: dict[str, Any] = field(default_factory=dict)
    role: str = "challenger"  # never 'live'; the pattern system has no live role


class ModelRegistry:
    def __init__(self) -> None:
        self._versions: dict[str, ModelVersion] = {}
        self._artifacts: dict[str, bytes] = {}
        self._active: str | None = None
        self._history: list[str] = []

    def register(self, version: ModelVersion, artifact: bytes) -> None:
        if version.version_id in self._versions:
            raise ValueError("version ids are immutable; choose a new id")
        if hashlib.sha256(artifact).hexdigest() != version.artifact_sha256:
            raise ValueError("artifact hash does not match the registered version")
        self._versions[version.version_id] = version
        self._artifacts[version.version_id] = artifact

    def load(self, version_id: str) -> bytes:
        artifact = self._artifacts[version_id]
        if hashlib.sha256(artifact).hexdigest() != self._versions[version_id].artifact_sha256:
            raise ValueError(f"artifact {version_id} is corrupt; refusing to load")  # fail closed (CP-0333)
        return artifact

    def promote(self, version_id: str, *, evidence: EvidenceTier) -> None:
        if evidence not in (EvidenceTier.FORWARD_PAPER, EvidenceTier.EXTERNALLY_CONFIRMED):
            raise PromotionRefused("promotion needs forward paper or external confirmation evidence")
        self.load(version_id)
        if self._active is not None:
            self._history.append(self._active)
        self._active = version_id

    def rollback(self) -> str:
        if not self._history:
            raise PromotionRefused("nothing to roll back to")
        self._active = self._history.pop()
        return self._active

    @property
    def active(self) -> str | None:
        return self._active

    def versions(self) -> list[str]:
        return sorted(self._versions)


MIN_LABELS_FOR_CALIBRATION = 200


def calibration(probs: list[float], outcomes: list[int]) -> dict[str, Any]:
    """Brier score when there are enough valid labels; otherwise NO_CALIBRATION (CP-0291)."""
    if len(probs) != len(outcomes):
        raise ValueError("probabilities and outcomes must align")
    if len(probs) < MIN_LABELS_FOR_CALIBRATION:
        return {"status": "NO_CALIBRATION", "n": len(probs)}
    brier = sum((p - o) ** 2 for p, o in zip(probs, outcomes, strict=True)) / len(probs)
    return {"status": "CALIBRATED_OUT_OF_TIME", "n": len(probs), "brier": brier}


def drift_status(reference: list[float], recent: list[float], *, threshold_sd: float = 2.0) -> str:
    """Mean shift in standard-deviation units raises REVIEW, never silent continuation (CP-0292)."""
    if len(reference) < 2 or not recent:
        return "INSUFFICIENT_DATA"
    mean = sum(reference) / len(reference)
    var = sum((x - mean) ** 2 for x in reference) / (len(reference) - 1)
    sd = var ** 0.5
    if sd == 0:
        return "REVIEW" if sum(recent) / len(recent) != mean else "OK"
    shift = abs(sum(recent) / len(recent) - mean) / sd
    return "REVIEW" if shift > threshold_sd else "OK"


def final_gate(*, deterministic_pass: bool, learned_score: float) -> bool:
    """A learned score can only lower a decision, never overturn a deterministic rejection (CP-0293)."""
    return deterministic_pass and learned_score >= 0.0


def proposal(hypothesis: str, *, remaining_budget: int, family_size: int) -> dict[str, Any]:
    """Automatic research proposal: it never executes a protected trial (CP-0294)."""
    return {
        "hypothesis": hypothesis,
        "executes_trial": False,
        "trial_budget_remaining": remaining_budget,
        "family_multiplicity_cost": f"{family_size} variants share the family error budget",
    }
