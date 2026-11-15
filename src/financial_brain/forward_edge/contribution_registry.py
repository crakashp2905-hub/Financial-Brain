"""Append-only registry for cohort-bound contribution reports."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

from .contribution_report import ContributionReportError, contribution_report
from .durable import DurableJournal
from .prospective_registry import ProspectiveCohortRegistry


class ContributionRegistryError(ValueError):
    """Contribution evidence is malformed or conflicts with its frozen cohort."""


def _digest(value: str, label: str) -> None:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ContributionRegistryError(f"{label} must be a lowercase SHA-256 digest")


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ContributionRegistryError("contribution evidence must be canonical JSON") from exc


class ContributionRegistry:
    """Freeze report inputs and source digests against a prospective cohort plan.

    Input hashes provide immutable identity only; they do not authenticate the
    source ledger or prove attribution correctness.
    """

    def __init__(self, path: Path, cohorts: ProspectiveCohortRegistry) -> None:
        self.journal = DurableJournal(Path(path))
        self.cohorts = cohorts
        self._records: dict[tuple[str, str], dict] = {}
        self._rebuild()

    @staticmethod
    def _identity(value: str, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ContributionRegistryError(f"{label} is required")
        return value.strip()

    def _validate(self, record: dict) -> dict:
        cohort_id = self._identity(record["cohort_id"], "cohort ID")
        report_id = self._identity(record["report_id"], "report ID")
        plan = self.cohorts.resolve(cohort_id)
        if record.get("protocol_sha256") != plan.plan_sha256:
            raise ContributionRegistryError("report protocol differs from frozen cohort plan")
        if tuple(record.get("scheduled_sessions", ())) != plan.confirmation_sessions:
            raise ContributionRegistryError("report schedule differs from frozen cohort plan")
        source_hashes = record.get("source_sha256")
        if not isinstance(source_hashes, dict) or set(source_hashes) != {"daily_returns", "contributions"}:
            raise ContributionRegistryError("daily returns and contributions require source digests")
        for name, value in source_hashes.items():
            _digest(value, f"{name} source digest")
        try:
            report = contribution_report(
                scheduled_sessions=cast(list[str], record["scheduled_sessions"]),
                daily_net_returns=record["daily_net_returns"],
                contributions=record["contributions"],
                protocol_sha256=record["protocol_sha256"],
                frozen_at_utc=record["frozen_at_utc"],
                first_observation_at_utc=record["first_observation_at_utc"],
            )
        except (ContributionReportError, KeyError, TypeError) as exc:
            raise ContributionRegistryError(f"invalid contribution report inputs: {exc}") from exc
        if record.get("report") != report:
            raise ContributionRegistryError("stored contribution report differs from frozen inputs")
        payload = {key: record[key] for key in (
            "cohort_id", "report_id", "protocol_sha256", "scheduled_sessions",
            "daily_net_returns", "contributions", "frozen_at_utc",
            "first_observation_at_utc", "source_sha256", "report",
        )}
        payload["cohort_id"] = cohort_id
        payload["report_id"] = report_id
        digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
        if record.get("evidence_sha256") != digest:
            raise ContributionRegistryError("contribution evidence digest mismatch")
        return {**payload, "evidence_sha256": digest}

    def _accept(self, record: dict) -> None:
        normalized = self._validate(record)
        key = (normalized["cohort_id"], normalized["report_id"])
        prior = self._records.get(key)
        if prior is not None and prior != normalized:
            raise ContributionRegistryError("registered contribution report is immutable")
        self._records[key] = normalized

    def _rebuild(self) -> None:
        for row in self.journal.records():
            if row.get("t") != "CONTRIBUTION_REPORT_REGISTERED":
                raise ContributionRegistryError("unknown contribution registry event")
            try:
                self._accept(row["record"])
            except (KeyError, TypeError) as exc:
                raise ContributionRegistryError("malformed contribution registry event") from exc

    def register(self, *, cohort_id: str, report_id: str,
                 daily_net_returns: dict[str, float | None], contributions: list[dict],
                 source_sha256: dict[str, str]) -> dict:
        cohort_id = self._identity(cohort_id, "cohort ID")
        report_id = self._identity(report_id, "report ID")
        plan = self.cohorts.resolve(cohort_id)
        if not isinstance(source_sha256, dict):
            raise ContributionRegistryError("source digests must be a mapping")
        record = {
            "cohort_id": cohort_id, "report_id": report_id,
            "protocol_sha256": plan.plan_sha256,
            "scheduled_sessions": list(plan.confirmation_sessions),
            "daily_net_returns": daily_net_returns, "contributions": contributions,
            "frozen_at_utc": plan.frozen_at_utc,
            "first_observation_at_utc": plan.first_confirmation_observation_at_utc,
            "source_sha256": {key: source_sha256[key] for key in sorted(source_sha256)},
        }
        try:
            report = contribution_report(
                scheduled_sessions=plan.confirmation_sessions,
                daily_net_returns=daily_net_returns, contributions=contributions,
                protocol_sha256=plan.plan_sha256, frozen_at_utc=plan.frozen_at_utc,
                first_observation_at_utc=plan.first_confirmation_observation_at_utc,
            )
        except ContributionReportError as exc:
            raise ContributionRegistryError(f"invalid contribution report inputs: {exc}") from exc
        record["report"] = report
        payload = {key: record[key] for key in (
            "cohort_id", "report_id", "protocol_sha256", "scheduled_sessions",
            "daily_net_returns", "contributions", "frozen_at_utc",
            "first_observation_at_utc", "source_sha256", "report",
        )}
        record["evidence_sha256"] = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
        normalized = self._validate(record)
        key = (cohort_id, report_id)
        prior = self._records.get(key)
        if prior is not None:
            if prior != normalized:
                raise ContributionRegistryError("registered contribution report is immutable")
            return {**prior["report"], "evidence_sha256": prior["evidence_sha256"]}
        self.journal.append({"t": "CONTRIBUTION_REPORT_REGISTERED", "record": normalized})
        self._records[key] = normalized
        return {**report, "evidence_sha256": normalized["evidence_sha256"]}

    def resolve(self, *, cohort_id: str, report_id: str) -> dict:
        key = (self._identity(cohort_id, "cohort ID"), self._identity(report_id, "report ID"))
        try:
            record = self._validate(self._records[key])
        except KeyError as exc:
            raise ContributionRegistryError("no registered contribution report") from exc
        return {**record["report"], "evidence_sha256": record["evidence_sha256"]}
