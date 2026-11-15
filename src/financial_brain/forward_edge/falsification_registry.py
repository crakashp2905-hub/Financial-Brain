"""Append-only registry tying falsification evidence to frozen prospective cohorts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .durable import DurableJournal
from .evidence_stats import FALSIFICATION_CHECKS, StatsError, falsification_report
from .prospective_registry import ProspectiveCohortRegistry


class FalsificationRegistryError(ValueError):
    """Falsification evidence is incomplete, malformed, or conflicts with a frozen record."""


def _digest(value: str, label: str) -> None:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise FalsificationRegistryError(f"{label} must be a lowercase SHA-256 digest")


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise FalsificationRegistryError("falsification evidence must be canonical JSON") from exc


def _seal(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


class FalsificationRegistry:
    """Durably freezes per-trial checks and artifact digests against a cohort plan hash.

    Artifact digests prove record identity only. The registry does not independently
    establish that the referenced checks ran or that their evidence is truthful.
    """

    def __init__(self, path: Path, cohort_registry: ProspectiveCohortRegistry) -> None:
        self.journal = DurableJournal(Path(path))
        self.cohort_registry = cohort_registry
        self._evidence: dict[tuple[str, str], dict] = {}
        self._rebuild()

    @staticmethod
    def _identity(value: str, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise FalsificationRegistryError(f"{label} is required")
        return value.strip()

    def _validate(self, record: dict) -> dict:
        trial_id = self._identity(record["trial_id"], "trial ID")
        cohort_id = self._identity(record["cohort_id"], "cohort ID")
        plan = self.cohort_registry.resolve(cohort_id)
        if record.get("cohort_plan_sha256") != plan.plan_sha256:
            raise FalsificationRegistryError("falsification evidence differs from the frozen cohort")
        results = record["results"]
        digests = record["artifact_sha256"]
        if not isinstance(results, dict) or set(results) != set(FALSIFICATION_CHECKS):
            raise FalsificationRegistryError("registered report must contain every falsification check")
        if not isinstance(digests, dict) or set(digests) - set(FALSIFICATION_CHECKS):
            raise FalsificationRegistryError("artifact digests contain an unknown falsification check")
        try:
            report = falsification_report(results)
        except StatsError as exc:
            raise FalsificationRegistryError(f"invalid falsification results: {exc}") from exc
        if record.get("report") != report:
            raise FalsificationRegistryError("registered falsification report differs from its results")
        for check, result in results.items():
            if result is not None and check not in digests:
                raise FalsificationRegistryError(f"{check} requires a source artifact digest")
            if check in digests:
                _digest(digests[check], f"{check} artifact digest")
        payload = {
            "trial_id": trial_id,
            "cohort_id": cohort_id,
            "cohort_plan_sha256": plan.plan_sha256,
            "results": {check: results[check] for check in FALSIFICATION_CHECKS},
            "artifact_sha256": {check: digests[check] for check in sorted(digests)},
            "report": report,
        }
        digest = _seal(payload)
        if record.get("evidence_sha256") != digest:
            raise FalsificationRegistryError("registered falsification evidence digest mismatch")
        return {**payload, "evidence_sha256": digest}

    def _accept(self, record: dict) -> None:
        normalized = self._validate(record)
        key = (normalized["cohort_id"], normalized["trial_id"])
        prior = self._evidence.get(key)
        if prior is not None and prior != normalized:
            raise FalsificationRegistryError("registered falsification evidence is immutable")
        self._evidence[key] = normalized

    def _rebuild(self) -> None:
        for row in self.journal.records():
            if row.get("t") != "FALSIFICATION_EVIDENCE_REGISTERED":
                raise FalsificationRegistryError("unknown falsification registry event")
            try:
                self._accept(row["evidence"])
            except (KeyError, TypeError) as exc:
                raise FalsificationRegistryError("malformed falsification registry event") from exc

    def register(self, *, trial_id: str, cohort_id: str,
                 results: dict[str, bool | None],
                 artifact_sha256: dict[str, str]) -> dict:
        trial_id = self._identity(trial_id, "trial ID")
        cohort_id = self._identity(cohort_id, "cohort ID")
        if not isinstance(results, dict) or not isinstance(artifact_sha256, dict):
            raise FalsificationRegistryError("results and artifact digests must be mappings")
        plan = self.cohort_registry.resolve(cohort_id)
        normalized_results = {check: results.get(check) for check in FALSIFICATION_CHECKS}
        unknown = set(results) - set(FALSIFICATION_CHECKS)
        if unknown:
            raise FalsificationRegistryError(f"unknown falsification checks: {sorted(unknown)}")
        report = falsification_report(normalized_results)
        payload = {
            "trial_id": trial_id,
            "cohort_id": cohort_id,
            "cohort_plan_sha256": plan.plan_sha256,
            "results": normalized_results,
            "artifact_sha256": {check: artifact_sha256[check]
                                for check in sorted(artifact_sha256)},
            "report": report,
        }
        digest = _seal(payload)
        record = {**payload, "evidence_sha256": digest}
        key = (cohort_id, trial_id)
        prior = self._evidence.get(key)
        if prior is not None:
            if prior != record:
                raise FalsificationRegistryError("registered falsification evidence is immutable")
            return {**prior["report"], "trial_id": trial_id, "cohort_id": cohort_id,
                    "cohort_plan_sha256": plan.plan_sha256,
                    "evidence_sha256": digest}
        normalized = self._validate(record)
        self.journal.append({"t": "FALSIFICATION_EVIDENCE_REGISTERED", "evidence": record})
        self._evidence[key] = normalized
        return {**report, "trial_id": trial_id, "cohort_id": cohort_id,
                "cohort_plan_sha256": plan.plan_sha256, "evidence_sha256": digest}

    def resolve(self, *, trial_id: str, cohort_id: str) -> dict:
        trial_id = self._identity(trial_id, "trial ID")
        cohort_id = self._identity(cohort_id, "cohort ID")
        try:
            record = self._evidence[(cohort_id, trial_id)]
        except KeyError as exc:
            raise FalsificationRegistryError("no registered falsification evidence") from exc
        normalized = self._validate(record)
        return {**normalized["report"], "trial_id": trial_id, "cohort_id": cohort_id,
                "cohort_plan_sha256": normalized["cohort_plan_sha256"],
                "evidence_sha256": normalized["evidence_sha256"]}
