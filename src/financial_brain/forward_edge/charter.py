"""Hashable experiment charter (FE-0004).

A charter freezes code hash, parameters, capital, dates, universe and benchmarks. Its canonical JSON hash is the
identity of the experiment. Any change produces a new version that links to the previous hash and starts a new
prospective clock: a revision cannot silently inherit the old observation window.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .decision_custody import Cohort, CohortSchedule
from .durable import DurableJournal

_SHA = re.compile(r"^[0-9a-f]{64}$")


class CharterError(ValueError):
    pass


@dataclass(frozen=True)
class EvaluationIdentity:
    """Content-addressed market, rights, model, strategy, risk, universe, cost and benchmark inputs."""

    strategy_code_sha256: str
    model_code_sha256: str
    data_snapshot_sha256: str
    data_rights_id: str
    data_rights_sha256: str
    market_calendar_id: str
    market_calendar_sha256: str
    universe_sha256: str
    execution_cost_id: str
    execution_cost_sha256: str
    risk_policy_sha256: str
    benchmarks: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        names = ("data_rights_id", "market_calendar_id", "execution_cost_id")
        if any(not isinstance(getattr(self, name), str) or not getattr(self, name)
               or getattr(self, name).strip() != getattr(self, name) for name in names):
            raise CharterError("data-rights, calendar and execution-cost identifiers are required")
        if not isinstance(self.benchmarks, tuple) or not self.benchmarks or any(
                not isinstance(item, tuple) or len(item) != 2 for item in self.benchmarks):
            raise CharterError("benchmark identities must be non-empty id/digest pairs")
        hashes = (self.strategy_code_sha256, self.model_code_sha256, self.data_snapshot_sha256,
                  self.data_rights_sha256, self.market_calendar_sha256, self.universe_sha256,
                  self.execution_cost_sha256, self.risk_policy_sha256, *(digest for _, digest in self.benchmarks))
        if any(not isinstance(value, str) or not _SHA.fullmatch(value) for value in hashes):
            raise CharterError("every evaluation identity component must have a SHA-256 digest")
        if not self.benchmarks or list(self.benchmarks) != sorted(self.benchmarks):
            raise CharterError("benchmark identities must be non-empty and sorted by id")
        if len({name for name, _ in self.benchmarks}) != len(self.benchmarks):
            raise CharterError("benchmark identities must be unique")
        if any(not isinstance(name, str) or not name or name.strip() != name for name, _ in self.benchmarks):
            raise CharterError("benchmark identifiers are required")

    def as_dict(self) -> dict:
        return {
            "strategy_code_sha256": self.strategy_code_sha256,
            "model_code_sha256": self.model_code_sha256,
            "data_snapshot_sha256": self.data_snapshot_sha256,
            "data_rights_id": self.data_rights_id,
            "data_rights_sha256": self.data_rights_sha256,
            "market_calendar_id": self.market_calendar_id,
            "market_calendar_sha256": self.market_calendar_sha256,
            "universe_sha256": self.universe_sha256,
            "execution_cost_id": self.execution_cost_id,
            "execution_cost_sha256": self.execution_cost_sha256,
            "risk_policy_sha256": self.risk_policy_sha256,
            "benchmarks": [list(item) for item in self.benchmarks],
        }

    @classmethod
    def from_dict(cls, value: dict) -> "EvaluationIdentity":
        expected = {"strategy_code_sha256", "model_code_sha256", "data_snapshot_sha256", "data_rights_id",
                    "data_rights_sha256", "market_calendar_id", "market_calendar_sha256", "universe_sha256",
                    "execution_cost_id", "execution_cost_sha256", "risk_policy_sha256", "benchmarks"}
        if not isinstance(value, dict) or set(value) != expected:
            raise CharterError("malformed evaluation identity")
        try:
            benchmarks = value["benchmarks"]
            if not isinstance(benchmarks, list) or any(not isinstance(i, (list, tuple)) or len(i) != 2
                                                       for i in benchmarks):
                raise CharterError("malformed benchmark identity")
            return cls(**{**value, "benchmarks": tuple((i[0], i[1]) for i in benchmarks)})
        except (IndexError, TypeError, ValueError) as exc:
            raise CharterError("malformed benchmark identity") from exc


@dataclass(frozen=True)
class ExperimentCharter:
    charter_id: str
    version: int
    strategy_id: str
    code_sha256: str
    capital_inr: str
    start_on: str
    end_on: str
    universe: tuple[str, ...]
    benchmarks: tuple[str, ...]
    horizon_sessions: int
    parameters: tuple[tuple[str, str], ...] = field(default=())
    prior_sha256: str | None = None
    evaluation_identity: EvaluationIdentity | None = None

    def __post_init__(self) -> None:
        if not self.charter_id.strip() or not self.strategy_id.strip():
            raise CharterError("charter and strategy ids are required")
        if self.version < 1:
            raise CharterError("version starts at 1")
        if (self.version == 1) != (self.prior_sha256 is None):
            raise CharterError("version 1 has no prior; later versions must name the prior charter hash")
        if self.prior_sha256 is not None and not _SHA.match(self.prior_sha256):
            raise CharterError("prior hash must be sha256 hex")
        if not _SHA.match(self.code_sha256):
            raise CharterError("code hash must be sha256 hex")
        try:
            capital = Decimal(self.capital_inr)
        except InvalidOperation as exc:
            raise CharterError("capital must be a decimal string") from exc
        if not capital.is_finite() or capital <= 0:
            raise CharterError("capital must be positive")
        start, end = date.fromisoformat(self.start_on), date.fromisoformat(self.end_on)
        if end <= start:
            raise CharterError("end must be after start")
        if not self.universe or list(self.universe) != sorted(set(self.universe)):
            raise CharterError("universe must be non-empty, unique and sorted")
        if not self.benchmarks:
            raise CharterError("at least one frozen benchmark is required")
        if self.horizon_sessions < 1:
            raise CharterError("horizon must be at least one session")
        if list(self.parameters) != sorted(self.parameters):
            raise CharterError("parameters must be sorted by name")
        if self.evaluation_identity is not None:
            if self.evaluation_identity.strategy_code_sha256 != self.code_sha256:
                raise CharterError("evaluation strategy-code digest must match charter code_sha256")
            if set(name for name, _ in self.evaluation_identity.benchmarks) != set(self.benchmarks):
                raise CharterError("evaluation benchmark digests must match the frozen charter benchmarks")

    def require_evaluation_identity(self) -> EvaluationIdentity:
        if self.evaluation_identity is None:
            raise CharterError("protected evaluation needs complete market/data-rights/model/cost/risk identity")
        return self.evaluation_identity

    def canonical_bytes(self) -> bytes:
        body = {
            "charter_id": self.charter_id, "version": self.version, "strategy_id": self.strategy_id,
            "code_sha256": self.code_sha256, "capital_inr": str(Decimal(self.capital_inr)),
            "start_on": self.start_on, "end_on": self.end_on, "universe": list(self.universe),
            "benchmarks": list(self.benchmarks), "horizon_sessions": self.horizon_sessions,
            "parameters": [list(p) for p in self.parameters], "prior_sha256": self.prior_sha256,
            "evaluation_identity": self.evaluation_identity.as_dict() if self.evaluation_identity else None,
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def revise(self, *, new_start_on: str, **changes) -> "ExperimentCharter":
        """A modification is a new version with a new prospective clock (its start must move forward)."""
        if date.fromisoformat(new_start_on) <= date.fromisoformat(self.start_on):
            raise CharterError("a revised charter needs a later start date: its clock is new")
        if not changes:
            raise CharterError("a revision must change something")
        return replace(self, version=self.version + 1, prior_sha256=self.sha256(), start_on=new_start_on, **changes)

    def as_dict(self) -> dict:
        return {
            "charter_id": self.charter_id, "version": self.version, "strategy_id": self.strategy_id,
            "code_sha256": self.code_sha256, "capital_inr": str(Decimal(self.capital_inr)),
            "start_on": self.start_on, "end_on": self.end_on, "universe": list(self.universe),
            "benchmarks": list(self.benchmarks), "horizon_sessions": self.horizon_sessions,
            "parameters": [list(p) for p in self.parameters], "prior_sha256": self.prior_sha256,
            "evaluation_identity": self.evaluation_identity.as_dict() if self.evaluation_identity else None,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "ExperimentCharter":
        expected = {"charter_id", "version", "strategy_id", "code_sha256", "capital_inr", "start_on", "end_on",
                    "universe", "benchmarks", "horizon_sessions", "parameters", "prior_sha256", "evaluation_identity"}
        if not isinstance(value, dict) or set(value) != expected:
            raise CharterError("malformed experiment charter")
        identity = value["evaluation_identity"]
        try:
            return cls(
                charter_id=value["charter_id"], version=value["version"], strategy_id=value["strategy_id"],
                code_sha256=value["code_sha256"], capital_inr=value["capital_inr"], start_on=value["start_on"],
                end_on=value["end_on"], universe=tuple(value["universe"]), benchmarks=tuple(value["benchmarks"]),
                horizon_sessions=value["horizon_sessions"], parameters=tuple(tuple(p) for p in value["parameters"]),
                prior_sha256=value["prior_sha256"],
                evaluation_identity=EvaluationIdentity.from_dict(identity) if identity is not None else None,
            )
        except (TypeError, KeyError) as exc:
            raise CharterError("malformed experiment charter") from exc


class CharterRegistry:
    """Append-only, hash-chained registry; every revision is a new prospective trial version."""

    def __init__(self, path: Path) -> None:
        self.journal = DurableJournal(Path(path))
        self._latest: dict[str, tuple[ExperimentCharter, CohortSchedule, str]] = {}
        self._closed: dict[tuple[str, str, str], tuple[str, str]] = {}
        self._rebuild()

    def _rebuild(self) -> None:
        for row in self.journal.records():
            if row.get("t") == "CHARTER_REGISTERED":
                charter = ExperimentCharter.from_dict(row["charter"])
                schedule = CohortSchedule(
                    charter_sha256=row["schedule"]["charter_sha256"],
                    cohorts=tuple(Cohort(*item) for item in row["schedule"]["cohorts"]),
                )
                if charter.sha256() != row["charter_sha256"] or schedule.sha256() != row["schedule_sha256"]:
                    raise CharterError("charter registry hash mismatch")
                self._accept(charter, schedule, row["charter_sha256"], replay=True)
            elif row.get("t") == "COHORT_CLOSED":
                key = (row["charter_id"], row["charter_sha256"], row["cohort_id"])
                self._accept_close(*key, closed_on=row["closed_on"], outcome_sha256=row["outcome_sha256"])
            else:
                raise CharterError("unknown charter registry event")

    def _accept(self, charter: ExperimentCharter, schedule: CohortSchedule, digest: str, *, replay: bool = False) -> None:
        prior = self._latest.get(charter.charter_id)
        if prior is None:
            if charter.version != 1 or charter.prior_sha256 is not None:
                raise CharterError("first registered charter must be version 1")
        else:
            previous, previous_schedule, previous_digest = prior
            if charter.version == previous.version and digest == previous_digest and schedule.sha256() == previous_schedule.sha256():
                return
            if charter.version != previous.version + 1 or charter.prior_sha256 != previous_digest:
                raise CharterError("changed strategy/evaluation identity needs the next version and prior hash")
            if date.fromisoformat(charter.start_on) <= date.fromisoformat(previous.start_on):
                raise CharterError("a revised charter needs a later prospective start date")
        if schedule.charter_sha256 != digest:
            raise CharterError("cohort schedule must bind to the exact charter hash")
        charter_start, charter_end = date.fromisoformat(charter.start_on), date.fromisoformat(charter.end_on)
        for cohort in schedule.cohorts:
            if date.fromisoformat(cohort.start) < charter_start or date.fromisoformat(cohort.end) > charter_end:
                raise CharterError("cohort endpoints must stay inside the registered charter window")
        if replay:
            self._latest[charter.charter_id] = (charter, schedule, digest)

    def register(self, charter: ExperimentCharter, schedule: CohortSchedule) -> str:
        charter.require_evaluation_identity()
        digest = charter.sha256()
        if schedule.charter_sha256 != digest:
            raise CharterError("cohort schedule must bind to the exact charter hash")
        prior = self._latest.get(charter.charter_id)
        if prior is not None and charter.version == prior[0].version and digest == prior[2] \
                and schedule.sha256() == prior[1].sha256():
            return digest
        self._accept(charter, schedule, digest)
        self.journal.append({"t": "CHARTER_REGISTERED", "charter": charter.as_dict(),
                             "charter_sha256": digest,
                             "schedule": {"charter_sha256": schedule.charter_sha256,
                                          "cohorts": [[c.cohort_id, c.start, c.end] for c in schedule.cohorts]},
                             "schedule_sha256": schedule.sha256()})
        self._latest[charter.charter_id] = (charter, schedule, digest)
        return digest

    def latest(self, charter_id: str) -> tuple[ExperimentCharter, CohortSchedule, str]:
        try:
            return self._latest[charter_id]
        except KeyError as exc:
            raise CharterError(f"unknown charter: {charter_id}") from exc

    def _accept_close(self, charter_id: str, charter_sha256: str, cohort_id: str, *, closed_on: str,
                      outcome_sha256: str) -> None:
        if not _SHA.fullmatch(outcome_sha256):
            raise CharterError("cohort outcome must be content-addressed")
        try:
            closed_date = date.fromisoformat(closed_on)
            charter, schedule, digest = self._latest[charter_id]
        except (ValueError, KeyError) as exc:
            raise CharterError("cohort close has an invalid date or unknown charter") from exc
        if digest != charter_sha256:
            raise CharterError("cohort close must bind to the latest registered charter")
        cohort = next((item for item in schedule.cohorts if item.cohort_id == cohort_id), None)
        if cohort is None or closed_date < date.fromisoformat(cohort.end):
            raise CharterError("cohort cannot close before its frozen endpoint")
        key = (charter_id, charter_sha256, cohort_id)
        value = (closed_on, outcome_sha256)
        if key in self._closed and self._closed[key] != value:
            raise CharterError("closed cohort outcome is immutable")
        self._closed[key] = value

    def record_cohort_close(self, charter_id: str, cohort_id: str, *, closed_on: str,
                            outcome_sha256: str) -> None:
        try:
            _, _, digest = self._latest[charter_id]
        except KeyError as exc:
            raise CharterError("unknown charter") from exc
        key = (charter_id, digest, cohort_id)
        value = (closed_on, outcome_sha256)
        if key in self._closed:
            if self._closed[key] != value:
                raise CharterError("closed cohort outcome is immutable")
            return
        self._accept_close(charter_id, digest, cohort_id, closed_on=closed_on, outcome_sha256=outcome_sha256)
        self.journal.append({"t": "COHORT_CLOSED", "charter_id": charter_id, "charter_sha256": digest,
                             "cohort_id": cohort_id, "closed_on": closed_on,
                             "outcome_sha256": outcome_sha256})
