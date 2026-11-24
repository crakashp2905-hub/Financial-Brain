"""Exit-complete and embargoed outcome release for training (FE-0124)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone


class OutcomeMaturationError(ValueError):
    """Outcome lineage or chronology is invalid."""


@dataclass(frozen=True)
class OutcomeCandidate:
    outcome_id: str
    strategy_id: str
    strategy_version: str
    decision_at: datetime
    features_sha256: str
    exit_complete_at: datetime | None
    embargo_until: datetime
    realized_net_return: float | None
    outcome_source_sha256: str | None

    def __post_init__(self) -> None:
        if not all(value.strip() for value in
                   (self.outcome_id, self.strategy_id, self.strategy_version)):
            raise OutcomeMaturationError("outcome and strategy identities are required")
        for label, digest in (("feature", self.features_sha256),
                              ("outcome", self.outcome_source_sha256)):
            if digest is not None and (len(digest) != 64 or any(
                    character not in "0123456789abcdef" for character in digest)):
                raise OutcomeMaturationError(f"{label} source hash must be lowercase SHA-256")
        if len(self.features_sha256) != 64:
            raise OutcomeMaturationError("feature snapshot needs a SHA-256 identity")
        for stamp in (self.decision_at, self.embargo_until):
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise OutcomeMaturationError("decision and embargo clocks must be timezone aware")
        if self.embargo_until < self.decision_at:
            raise OutcomeMaturationError("embargo cannot predate the decision")
        if self.exit_complete_at is not None:
            if self.exit_complete_at.tzinfo is None or self.exit_complete_at.utcoffset() is None:
                raise OutcomeMaturationError("exit completion clock must be timezone aware")
            if self.exit_complete_at < self.decision_at:
                raise OutcomeMaturationError("full exit cannot predate the decision")
        if self.realized_net_return is not None and not math.isfinite(self.realized_net_return):
            raise OutcomeMaturationError("realized net return must be finite")
        if self.realized_net_return is not None and self.outcome_source_sha256 is None:
            raise OutcomeMaturationError("a realized label requires an outcome source hash")
        if self.exit_complete_at is None and self.realized_net_return is not None:
            raise OutcomeMaturationError("unfinished outcomes cannot carry a realized training label")


@dataclass(frozen=True)
class MaturedTrainingExample:
    outcome_id: str
    strategy_id: str
    strategy_version: str
    decision_at: datetime
    features_sha256: str
    realized_net_return: float
    outcome_source_sha256: str
    matured_at: datetime


@dataclass(frozen=True)
class PendingOutcome:
    outcome_id: str
    status: str
    release_at: datetime | None


@dataclass(frozen=True)
class OutcomeMaturationReport:
    as_of: datetime
    matured: tuple[MaturedTrainingExample, ...]
    pending: tuple[PendingOutcome, ...]
    report_hash: str


def build_outcome_maturation_queue(*, candidates: tuple[OutcomeCandidate, ...],
                                   as_of: datetime) -> OutcomeMaturationReport:
    """Release only labels with a completed exit, outcome hash and elapsed embargo."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise OutcomeMaturationError("as_of must be timezone aware")
    ids = [candidate.outcome_id for candidate in candidates]
    if len(ids) != len(set(ids)):
        raise OutcomeMaturationError("outcome IDs must be unique")
    matured: list[MaturedTrainingExample] = []
    pending: list[PendingOutcome] = []
    for candidate in sorted(candidates, key=lambda item: item.outcome_id):
        if candidate.exit_complete_at is None:
            pending.append(PendingOutcome(candidate.outcome_id, "EXIT_INCOMPLETE", None))
            continue
        release_at = max(candidate.exit_complete_at, candidate.embargo_until)
        if candidate.exit_complete_at > as_of:
            pending.append(PendingOutcome(candidate.outcome_id, "EXIT_NOT_YET_COMPLETED", release_at))
            continue
        if candidate.realized_net_return is None or candidate.outcome_source_sha256 is None:
            pending.append(PendingOutcome(candidate.outcome_id, "LABEL_SOURCE_MISSING", None))
            continue
        if release_at > as_of:
            pending.append(PendingOutcome(candidate.outcome_id, "EMBARGO_ACTIVE", release_at))
            continue
        matured.append(MaturedTrainingExample(
            candidate.outcome_id, candidate.strategy_id, candidate.strategy_version,
            candidate.decision_at.astimezone(timezone.utc), candidate.features_sha256,
            candidate.realized_net_return, candidate.outcome_source_sha256,
            release_at.astimezone(timezone.utc)))
    body = {"as_of": as_of.astimezone(timezone.utc).isoformat(),
            "matured": [{"outcome_id": row.outcome_id, "strategy_id": row.strategy_id,
                         "strategy_version": row.strategy_version,
                         "decision_at": row.decision_at.isoformat(),
                         "features_sha256": row.features_sha256,
                         "realized_net_return": row.realized_net_return,
                         "outcome_source_sha256": row.outcome_source_sha256,
                         "matured_at": row.matured_at.isoformat()} for row in matured],
            "pending": [{"outcome_id": row.outcome_id, "status": row.status,
                         "release_at": row.release_at.astimezone(timezone.utc).isoformat()
                         if row.release_at else None} for row in pending]}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                       allow_nan=False).encode()).hexdigest()
    return OutcomeMaturationReport(as_of.astimezone(timezone.utc), tuple(matured),
                                   tuple(pending), digest)
