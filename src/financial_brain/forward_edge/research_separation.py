"""Locked-cohort boundaries between diagnostics, ablations, paper and promotion (FE-0126)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
from pathlib import Path


class ResearchSeparationError(ValueError):
    """A research run crosses a frozen evidence boundary."""


class ResearchPurpose(StrEnum):
    LEARNING_DIAGNOSTIC = "LEARNING_DIAGNOSTIC"
    OFFLINE_ABLATION = "OFFLINE_ABLATION"
    PAPER_CHALLENGER = "PAPER_CHALLENGER"
    PROMOTION_VALIDATION = "PROMOTION_VALIDATION"


@dataclass(frozen=True)
class LockedEvaluationCohort:
    cohort_id: str
    frozen_at: datetime
    evaluation_sessions: tuple[date, ...]
    data_sha256: str
    frozen_model_sha256: str
    strategy_spec_sha256: str

    def __post_init__(self) -> None:
        if not self.cohort_id.strip():
            raise ResearchSeparationError("cohort ID is required")
        if self.frozen_at.tzinfo is None or self.frozen_at.utcoffset() is None:
            raise ResearchSeparationError("cohort freeze time must be timezone aware")
        if not self.evaluation_sessions or tuple(sorted(set(self.evaluation_sessions))) != self.evaluation_sessions:
            raise ResearchSeparationError("cohort sessions must be non-empty, unique and chronological")
        if self.frozen_at.date() >= self.evaluation_sessions[0]:
            raise ResearchSeparationError("evaluation cohort must be frozen before its first session")
        for name, digest in (("data", self.data_sha256), ("model", self.frozen_model_sha256),
                             ("strategy specification", self.strategy_spec_sha256)):
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ResearchSeparationError(f"{name} hash must be lowercase SHA-256")

    def digest(self) -> str:
        body = {"cohort_id": self.cohort_id,
                "frozen_at": self.frozen_at.astimezone(timezone.utc).isoformat(),
                "evaluation_sessions": [item.isoformat() for item in self.evaluation_sessions],
                "data_sha256": self.data_sha256,
                "frozen_model_sha256": self.frozen_model_sha256,
                "strategy_spec_sha256": self.strategy_spec_sha256}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class ResearchRun:
    run_id: str
    purpose: ResearchPurpose
    artifact_namespace: str
    training_sessions: tuple[date, ...]
    evaluation_sessions: tuple[date, ...]
    searched_hyperparameters: bool
    model_sha256: str
    strategy_spec_sha256: str
    labels_used_at_decision: bool = False
    evaluation_data_sha256: str | None = None


@dataclass(frozen=True)
class ResearchAuthorization:
    allowed: bool
    status: str
    reasons: tuple[str, ...]
    run_hash: str


def authorize_research_run(run: ResearchRun, cohort: LockedEvaluationCohort) -> ResearchAuthorization:
    """Enforce purpose-separated artifacts and prohibit tuning on locked/forward outcomes."""
    if not run.run_id.strip():
        raise ResearchSeparationError("run ID is required")
    for name, digest in (("model", run.model_sha256), ("strategy specification", run.strategy_spec_sha256)):
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ResearchSeparationError(f"run {name} hash must be lowercase SHA-256")
    if len(set(run.training_sessions)) != len(run.training_sessions):
        raise ResearchSeparationError("training sessions cannot be duplicated")
    if len(set(run.evaluation_sessions)) != len(run.evaluation_sessions):
        raise ResearchSeparationError("run evaluation sessions cannot be duplicated")
    if run.evaluation_data_sha256 is not None and (len(run.evaluation_data_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in run.evaluation_data_sha256)):
        raise ResearchSeparationError("evaluation data hash must be lowercase SHA-256")
    reasons: set[str] = set()
    if run.artifact_namespace != run.purpose.value.lower():
        reasons.add("ARTIFACT_NAMESPACE_MISMATCH")
    locked = set(cohort.evaluation_sessions)
    train = set(run.training_sessions)
    evaluate = set(run.evaluation_sessions)
    if train & locked:
        reasons.add("LOCKED_COHORT_CONTAMINATION")
    if train and evaluate and max(train) >= min(evaluate):
        reasons.add("TRAINING_SESSIONS_MUST_PRECEDE_EVALUATION")
    if run.purpose in {ResearchPurpose.LEARNING_DIAGNOSTIC, ResearchPurpose.OFFLINE_ABLATION}:
        if evaluate & locked:
            reasons.add("LOCKED_COHORT_CONTAMINATION")
        if run.evaluation_sessions:
            reasons.add("DIAGNOSTIC_EVALUATION_DATA_MUST_USE_SEPARATE_COHORT")
    elif run.purpose is ResearchPurpose.PAPER_CHALLENGER:
        if not evaluate <= locked:
            reasons.add("PAPER_SESSIONS_MUST_BELONG_TO_LOCKED_COHORT")
        if run.searched_hyperparameters:
            reasons.add("PAPER_CHALLENGER_PARAMETERS_MUST_BE_FROZEN")
        if run.model_sha256 != cohort.frozen_model_sha256:
            reasons.add("MODEL_DIFFERS_FROM_FROZEN_COHORT")
        if run.strategy_spec_sha256 != cohort.strategy_spec_sha256:
            reasons.add("STRATEGY_SPEC_DIFFERS_FROM_FROZEN_COHORT")
        if run.labels_used_at_decision:
            reasons.add("FORWARD_LABEL_LOOKAHEAD")
    elif run.purpose is ResearchPurpose.PROMOTION_VALIDATION:
        if run.searched_hyperparameters:
            reasons.add("PROMOTION_VALIDATION_CANNOT_TUNE")
        if evaluate != locked:
            reasons.add("PROMOTION_SESSIONS_MUST_MATCH_LOCKED_COHORT")
        if run.model_sha256 != cohort.frozen_model_sha256:
            reasons.add("MODEL_DIFFERS_FROM_FROZEN_COHORT")
        if run.evaluation_data_sha256 != cohort.data_sha256:
            reasons.add("EVALUATION_DATA_DIFFERS_FROM_LOCKED_COHORT")
        if run.strategy_spec_sha256 != cohort.strategy_spec_sha256:
            reasons.add("STRATEGY_SPEC_DIFFERS_FROM_FROZEN_COHORT")
        if run.labels_used_at_decision:
            reasons.add("FORWARD_LABEL_LOOKAHEAD")
    body = {"run_id": run.run_id, "purpose": run.purpose.value,
            "artifact_namespace": run.artifact_namespace,
            "training_sessions": [item.isoformat() for item in run.training_sessions],
            "evaluation_sessions": [item.isoformat() for item in run.evaluation_sessions],
            "searched_hyperparameters": run.searched_hyperparameters,
            "model_sha256": run.model_sha256,
            "strategy_spec_sha256": run.strategy_spec_sha256,
            "labels_used_at_decision": run.labels_used_at_decision,
            "evaluation_data_sha256": run.evaluation_data_sha256,
            "cohort_sha256": cohort.digest()}
    run_hash = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()
    ordered = tuple(sorted(reasons))
    return ResearchAuthorization(not ordered, "AUTHORIZED" if not ordered else "REFUSED",
                                 ordered, run_hash)


class LockedCohortRegistry:
    """Append-only local registry preventing a cohort ID from being redefined."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("CREATE TABLE IF NOT EXISTS locked_cohorts (cohort_id TEXT PRIMARY KEY, digest TEXT NOT NULL, body TEXT NOT NULL)")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS locked_cohorts_no_update BEFORE UPDATE ON locked_cohorts BEGIN SELECT RAISE(ABORT,'locked cohorts are immutable'); END")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS locked_cohorts_no_delete BEFORE DELETE ON locked_cohorts BEGIN SELECT RAISE(ABORT,'locked cohorts are immutable'); END")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def freeze(self, cohort: LockedEvaluationCohort) -> bool:
        body = {"cohort_id": cohort.cohort_id,
                "frozen_at": cohort.frozen_at.astimezone(timezone.utc).isoformat(),
                "evaluation_sessions": [item.isoformat() for item in cohort.evaluation_sessions],
                "data_sha256": cohort.data_sha256,
                "frozen_model_sha256": cohort.frozen_model_sha256,
                "strategy_spec_sha256": cohort.strategy_spec_sha256}
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        prior = self._con.execute("SELECT digest,body FROM locked_cohorts WHERE cohort_id=?",
                                  [cohort.cohort_id]).fetchone()
        if prior:
            if prior != (cohort.digest(), encoded):
                raise ResearchSeparationError("cohort ID is immutable and already has different content")
            return False
        self._con.execute("INSERT INTO locked_cohorts VALUES (?,?,?)",
                          [cohort.cohort_id, cohort.digest(), encoded])
        self._con.commit()
        return True
