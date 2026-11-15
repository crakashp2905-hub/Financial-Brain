"""Durable fixed-look policy registration bound to a frozen experiment charter."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from financial_brain.forward_edge.charter import CharterError, CharterRegistry
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.fixed_look import (
    FixedLookError,
    FixedLookPolicy,
    evaluate_look,
)
from financial_brain.forward_edge.sample_size import SampleSizeError, SampleSizePlan


class FixedLookRegistryError(ValueError):
    """A fixed-look policy is missing, mutable, or inconsistent with its charter."""


class FixedLookRegistry:
    """Append-only policy registry that pins each policy to the charter risk identity."""

    def __init__(self, path: Path, charter_registry: CharterRegistry) -> None:
        self.journal = DurableJournal(Path(path))
        self.charter_registry = charter_registry
        self._policies: dict[str, tuple[FixedLookPolicy, SampleSizePlan, str, str]] = {}
        self._rebuild()

    @staticmethod
    def bundle_sha256(policy: FixedLookPolicy, sample_size_plan: SampleSizePlan) -> str:
        body = {"fixed_look_policy_sha256": policy.sha256(),
                "sample_size_plan_sha256": sample_size_plan.sha256()}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_design(policy: FixedLookPolicy, sample_size_plan: SampleSizePlan) -> None:
        if not isinstance(sample_size_plan, SampleSizePlan):
            raise FixedLookRegistryError("a derived sample-size plan is required")
        try:
            if SampleSizePlan.from_dict(sample_size_plan.as_dict()) != sample_size_plan:
                raise FixedLookRegistryError("sample-size plan differs from its derived requirements")
        except SampleSizeError as exc:
            raise FixedLookRegistryError("sample-size plan differs from its derived requirements") from exc
        if sample_size_plan.familywise_alpha != policy.familywise_alpha:
            raise FixedLookRegistryError("fixed-look and sample-size alpha must match")
        if sample_size_plan.planned_looks != len(policy.checkpoints):
            raise FixedLookRegistryError("sample-size plan must use the registered fixed-look count")
        if sample_size_plan.frozen_at_utc != policy.frozen_at_utc:
            raise FixedLookRegistryError("sample-size and fixed-look freeze times must match")
        if sample_size_plan.first_observation_at_utc != policy.first_observation_at_utc:
            raise FixedLookRegistryError("sample-size and fixed-look observation times must match")
        if policy.checkpoints[-1] < sample_size_plan.required_sessions:
            raise FixedLookRegistryError("fixed-look schedule ends before the required sample size")

    def _validate_charter(self, policy: FixedLookPolicy, charter_id: str,
                          sample_size_plan: SampleSizePlan,
                          charter_sha256: str | None = None) -> str:
        self._validate_design(policy, sample_size_plan)
        try:
            if charter_sha256 is None:
                charter, _schedule, digest = self.charter_registry.latest(charter_id)
            else:
                charter, _schedule, digest = self.charter_registry.registered_revision(
                    charter_id, charter_sha256)
            identity = charter.require_evaluation_identity()
        except CharterError as exc:
            raise FixedLookRegistryError("fixed-look policy requires a registered evaluation charter") from exc
        if identity.risk_policy_sha256 != self.bundle_sha256(policy, sample_size_plan):
            raise FixedLookRegistryError("fixed-look and sample-size hashes differ from the charter risk identity")
        return digest

    def _accept(self, policy: FixedLookPolicy, sample_size_plan: SampleSizePlan,
                charter_id: str, charter_sha256: str,
                *, replay: bool = False) -> None:
        self._validate_charter(policy, charter_id, sample_size_plan, charter_sha256)
        prior = self._policies.get(policy.policy_id)
        value = (policy, sample_size_plan, charter_id, charter_sha256)
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
                sample_size_plan = SampleSizePlan.from_dict(row["sample_size_plan"])
                if policy.sha256() != row["policy_sha256"]:
                    raise FixedLookRegistryError("fixed-look policy digest mismatch")
                if sample_size_plan.sha256() != row["sample_size_plan_sha256"]:
                    raise FixedLookRegistryError("sample-size plan digest mismatch")
                if self.bundle_sha256(policy, sample_size_plan) != row["risk_policy_bundle_sha256"]:
                    raise FixedLookRegistryError("risk-policy bundle digest mismatch")
                self._accept(policy, sample_size_plan, row["charter_id"], row["charter_sha256"], replay=True)
            except (KeyError, TypeError, FixedLookError, SampleSizeError) as exc:
                raise FixedLookRegistryError("malformed fixed-look registry event") from exc

    def register(self, policy: FixedLookPolicy, *, sample_size_plan: SampleSizePlan,
                 charter_id: str) -> str:
        if not isinstance(policy, FixedLookPolicy) or not isinstance(sample_size_plan, SampleSizePlan):
            raise FixedLookRegistryError("validated fixed-look and sample-size policies are required")
        charter_sha256 = self._validate_charter(policy, charter_id, sample_size_plan)
        prior = self._policies.get(policy.policy_id)
        value = (policy, sample_size_plan, charter_id, charter_sha256)
        if prior is not None:
            if prior != value:
                raise FixedLookRegistryError("registered fixed-look policy is immutable")
            return self.bundle_sha256(policy, sample_size_plan)
        self._accept(policy, sample_size_plan, charter_id, charter_sha256)
        self.journal.append({
            "t": "FIXED_LOOK_POLICY_REGISTERED",
            "policy": policy.as_dict(),
            "policy_sha256": policy.sha256(),
            "sample_size_plan": sample_size_plan.as_dict(),
            "sample_size_plan_sha256": sample_size_plan.sha256(),
            "risk_policy_bundle_sha256": self.bundle_sha256(policy, sample_size_plan),
            "charter_id": charter_id,
            "charter_sha256": charter_sha256,
        })
        self._policies[policy.policy_id] = value
        return self.bundle_sha256(policy, sample_size_plan)

    def resolve(self, policy_id: str) -> FixedLookPolicy:
        policy, _sample_size_plan, _charter_id, _charter_sha256 = self._resolve_registered(policy_id)
        return policy

    def _resolve_registered(self, policy_id: str) -> tuple[FixedLookPolicy, SampleSizePlan, str, str]:
        try:
            policy, sample_size_plan, charter_id, charter_sha256 = self._policies[policy_id]
        except KeyError as exc:
            raise FixedLookRegistryError("unknown fixed-look policy") from exc
        self._validate_charter(policy, charter_id, sample_size_plan, charter_sha256)
        try:
            _charter, _schedule, latest_sha256 = self.charter_registry.latest(charter_id)
        except CharterError as exc:
            raise FixedLookRegistryError("fixed-look policy requires a registered evaluation charter") from exc
        if latest_sha256 != charter_sha256:
            raise FixedLookRegistryError("fixed-look policy is not bound to the current charter revision")
        return policy, sample_size_plan, charter_id, charter_sha256

    def evaluate(self, policy_id: str, observation: dict) -> dict:
        policy, sample_size_plan, _charter_id, _charter_sha256 = self._resolve_registered(policy_id)
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
        report["sample_size_plan_sha256"] = sample_size_plan.sha256()
        report["required_sessions"] = sample_size_plan.required_sessions
        if (report["registered_look"] and observation["completed_sessions"]
                < sample_size_plan.required_sessions):
            report["status"] = "SAMPLE_SIZE_NOT_MET"
            report["efficacy_decision"] = "WITHHELD"
        return report
