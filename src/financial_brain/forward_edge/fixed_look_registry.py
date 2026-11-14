"""Durable fixed-look policy registration bound to a frozen experiment charter."""
from __future__ import annotations

from pathlib import Path

from financial_brain.forward_edge.charter import CharterError, CharterRegistry
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.fixed_look import (
    FixedLookError,
    FixedLookPolicy,
    evaluate_look,
)


class FixedLookRegistryError(ValueError):
    """A fixed-look policy is missing, mutable, or inconsistent with its charter."""


class FixedLookRegistry:
    """Append-only policy registry that pins each policy to the charter risk identity."""

    def __init__(self, path: Path, charter_registry: CharterRegistry) -> None:
        self.journal = DurableJournal(Path(path))
        self.charter_registry = charter_registry
        self._policies: dict[str, tuple[FixedLookPolicy, str, str]] = {}
        self._rebuild()

    def _validate_charter(self, policy: FixedLookPolicy, charter_id: str,
                          charter_sha256: str | None = None) -> str:
        try:
            if charter_sha256 is None:
                charter, _schedule, digest = self.charter_registry.latest(charter_id)
            else:
                charter, _schedule, digest = self.charter_registry.registered_revision(
                    charter_id, charter_sha256)
            identity = charter.require_evaluation_identity()
        except CharterError as exc:
            raise FixedLookRegistryError("fixed-look policy requires a registered evaluation charter") from exc
        if identity.risk_policy_sha256 != policy.sha256():
            raise FixedLookRegistryError("fixed-look policy hash differs from the charter risk identity")
        return digest

    def _accept(self, policy: FixedLookPolicy, charter_id: str, charter_sha256: str,
                *, replay: bool = False) -> None:
        self._validate_charter(policy, charter_id, charter_sha256)
        prior = self._policies.get(policy.policy_id)
        value = (policy, charter_id, charter_sha256)
        if prior is not None and prior != value:
            raise FixedLookRegistryError("registered fixed-look policy is immutable")
        if replay:
            self._policies[policy.policy_id] = value

    def _rebuild(self) -> None:
        for row in self.journal.records():
            if row.get("t") != "FIXED_LOOK_POLICY_REGISTERED":
                raise FixedLookRegistryError("unknown fixed-look registry event")
            try:
                policy = FixedLookPolicy.from_dict(row["policy"])
                if policy.sha256() != row["policy_sha256"]:
                    raise FixedLookRegistryError("fixed-look policy digest mismatch")
                self._accept(policy, row["charter_id"], row["charter_sha256"], replay=True)
            except (KeyError, TypeError, FixedLookError) as exc:
                raise FixedLookRegistryError("malformed fixed-look registry event") from exc

    def register(self, policy: FixedLookPolicy, *, charter_id: str) -> str:
        if not isinstance(policy, FixedLookPolicy):
            raise FixedLookRegistryError("a validated fixed-look policy is required")
        charter_sha256 = self._validate_charter(policy, charter_id)
        prior = self._policies.get(policy.policy_id)
        value = (policy, charter_id, charter_sha256)
        if prior is not None:
            if prior != value:
                raise FixedLookRegistryError("registered fixed-look policy is immutable")
            return policy.sha256()
        self._accept(policy, charter_id, charter_sha256)
        self.journal.append({
            "t": "FIXED_LOOK_POLICY_REGISTERED",
            "policy": policy.as_dict(),
            "policy_sha256": policy.sha256(),
            "charter_id": charter_id,
            "charter_sha256": charter_sha256,
        })
        self._policies[policy.policy_id] = value
        return policy.sha256()

    def resolve(self, policy_id: str) -> FixedLookPolicy:
        try:
            policy, charter_id, charter_sha256 = self._policies[policy_id]
        except KeyError as exc:
            raise FixedLookRegistryError("unknown fixed-look policy") from exc
        self._validate_charter(policy, charter_id, charter_sha256)
        try:
            _charter, _schedule, latest_sha256 = self.charter_registry.latest(charter_id)
        except CharterError as exc:
            raise FixedLookRegistryError("fixed-look policy requires a registered evaluation charter") from exc
        if latest_sha256 != charter_sha256:
            raise FixedLookRegistryError("fixed-look policy is not bound to the current charter revision")
        return policy

    def evaluate(self, policy_id: str, observation: dict) -> dict:
        policy = self.resolve(policy_id)
        if not isinstance(observation, dict):
            raise FixedLookRegistryError("fixed-look observation must be a mapping")
        try:
            report = evaluate_look(
                policy,
                completed_sessions=observation["completed_sessions"],
                lower_confidence_bound=observation.get("lower_confidence_bound"),
                observed_max_drawdown=observation.get("observed_max_drawdown"),
            )
        except (KeyError, FixedLookError, TypeError) as exc:
            raise FixedLookRegistryError("fixed-look observation is invalid") from exc
        return report
