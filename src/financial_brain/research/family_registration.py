"""Temporal controls for prior-class and family-size evidence (ADR-0007 / PA-06-05).

An evaluation result cannot be certified as forward evidence unless its prior class and
family size were frozen before outcome access. Historical rescoring stays descriptive.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Callable

GENESIS = "0" * 64


class PriorClass(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class EvidenceDisposition(StrEnum):
    FORWARD_REGISTERED = "FORWARD_REGISTERED"
    RETROSPECTIVE_ONLY = "RETROSPECTIVE_ONLY"


class FamilyRegistrationError(ValueError):
    """Malformed registration or invalid event chronology."""


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FamilyRegistrationError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class FamilyRegistration:
    registration_id: str
    hypothesis_id: str
    prior_class: PriorClass
    family_size: int
    registered_at: datetime
    preregistration_sha256: str

    def __post_init__(self) -> None:
        if not all(isinstance(v, str) and v.strip() for v in (self.registration_id, self.hypothesis_id)):
            raise FamilyRegistrationError("registration and hypothesis IDs are required")
        if not isinstance(self.prior_class, PriorClass):
            raise FamilyRegistrationError("prior class must use the registered A/B/C enum")
        if type(self.family_size) is not int or self.family_size < 1:
            raise FamilyRegistrationError("family size must be a positive integer fixed at registration")
        _utc(self.registered_at, "registration clock")
        digest = self.preregistration_sha256
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise FamilyRegistrationError("preregistration must be identified by lowercase SHA-256")

    def identity(self) -> dict[str, object]:
        """Canonical, hashable registration identity for linking every scored result."""
        return {
            "registration_id": self.registration_id,
            "hypothesis_id": self.hypothesis_id,
            "prior_class": self.prior_class.value,
            "family_size": self.family_size,
            "registered_at": _utc(self.registered_at, "registration clock").isoformat(),
            "preregistration_sha256": self.preregistration_sha256,
        }

    def sha256(self) -> str:
        raw = json.dumps(self.identity(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FamilyEvaluation:
    registration: FamilyRegistration
    first_outcome_access_at: datetime
    scored_at: datetime
    result_sha256: str
    outcome_available_at: datetime | None = None

    def __post_init__(self) -> None:
        access = _utc(self.first_outcome_access_at, "first outcome access clock")
        scored = _utc(self.scored_at, "score clock")
        if scored < access:
            raise FamilyRegistrationError("score cannot precede first outcome access")
        available = _utc(self.outcome_available_at or self.first_outcome_access_at,
                         "outcome availability clock")
        if available > access:
            raise FamilyRegistrationError("outcome cannot be accessed before its availability clock")
        if len(self.result_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.result_sha256):
            raise FamilyRegistrationError("result must be identified by lowercase SHA-256")

    @property
    def disposition(self) -> EvidenceDisposition:
        registered = _utc(self.registration.registered_at, "registration clock")
        available = _utc(self.outcome_available_at or self.first_outcome_access_at,
                         "outcome availability clock")
        return (EvidenceDisposition.FORWARD_REGISTERED if registered < available
                else EvidenceDisposition.RETROSPECTIVE_ONLY)

    @property
    def may_certify_forward(self) -> bool:
        return self.disposition is EvidenceDisposition.FORWARD_REGISTERED

    def audit_record(self) -> dict[str, object]:
        return {
            "registration": self.registration.identity(),
            "registration_sha256": self.registration.sha256(),
            "outcome_available_at": _utc(self.outcome_available_at or self.first_outcome_access_at,
                                         "outcome availability clock").isoformat(),
            "first_outcome_access_at": _utc(self.first_outcome_access_at, "first outcome access clock").isoformat(),
            "scored_at": _utc(self.scored_at, "score clock").isoformat(),
            "result_sha256": self.result_sha256,
            "disposition": self.disposition.value,
            "may_certify_forward": self.may_certify_forward,
        }


class FamilyRegistrationLedger:
    """Append-only local ledger for preregistration, first outcome access and scoring.

    Registration time is assigned by the ledger clock, never accepted from the caller.
    This prevents a caller from backdating a prior/family-size decision in this ledger. It
    remains a local audit boundary; independent clock attestation and mandatory evaluator
    integration are deployment responsibilities.
    """

    def __init__(self, path: Path | str, *, clock: Callable[[], datetime] | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS family_registration_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            registration_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            body_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL UNIQUE)""")
        self._con.execute("CREATE INDEX IF NOT EXISTS family_registration_lookup "
                          "ON family_registration_events(registration_id, sequence)")

    def close(self) -> None:
        self._con.close()

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise FamilyRegistrationError("ledger clock must return a datetime")
        return _utc(value, "ledger clock")

    def _append(self, registration_id: str, event_type: str, body: dict[str, object]) -> str:
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        tail = self._con.execute(
            "SELECT event_hash FROM family_registration_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous = tail[0] if tail else GENESIS
        digest = hashlib.sha256((previous + encoded).encode("utf-8")).hexdigest()
        self._con.execute(
            "INSERT INTO family_registration_events "
            "(registration_id,event_type,body_json,previous_hash,event_hash) VALUES (?,?,?,?,?)",
            (registration_id, event_type, encoded, previous, digest),
        )
        return digest

    def register(self, *, hypothesis_id: str, prior_class: PriorClass, family_size: int,
                 preregistration_sha256: str) -> FamilyRegistration:
        """Freeze the evidence prior and full family size at a locally observed timestamp."""
        registration = FamilyRegistration(
            uuid.uuid4().hex, hypothesis_id, prior_class, family_size, self._now(), preregistration_sha256
        )
        self._con.execute("BEGIN IMMEDIATE")
        try:
            if self._con.execute(
                "SELECT 1 FROM family_registration_events WHERE registration_id=? LIMIT 1",
                (registration.registration_id,),
            ).fetchone():
                raise FamilyRegistrationError("registration ID collision")
            self._append(registration.registration_id, "REGISTERED", registration.identity())
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return registration

    def _events(self, registration_id: str) -> list[tuple[str, dict[str, object]]]:
        rows = self._con.execute(
            "SELECT event_type,body_json FROM family_registration_events "
            "WHERE registration_id=? ORDER BY sequence", (registration_id,),
        ).fetchall()
        return [(kind, json.loads(body)) for kind, body in rows]

    def record_outcome_access(self, registration_id: str, *, dataset_sha256: str,
                              outcome_available_at: datetime) -> datetime:
        """Record availability and first access; prior-available outcomes remain retrospective."""
        if not isinstance(dataset_sha256, str) or len(dataset_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in dataset_sha256
        ):
            raise FamilyRegistrationError("dataset identity must be lowercase SHA-256")
        observed_at = self._now()
        available_at = _utc(outcome_available_at, "outcome availability clock")
        self._con.execute("BEGIN IMMEDIATE")
        try:
            events = self._events(registration_id)
            if not events or events[0][0] != "REGISTERED":
                raise FamilyRegistrationError("outcome access requires a persisted registration")
            if available_at > observed_at:
                raise FamilyRegistrationError("outcome availability cannot be after first access")
            if any(kind == "OUTCOME_ACCESS" for kind, _ in events):
                raise FamilyRegistrationError("first outcome access is already recorded")
            self._append(registration_id, "OUTCOME_ACCESS", {
                "dataset_sha256": dataset_sha256, "outcome_available_at": available_at.isoformat(),
                "first_outcome_access_at": observed_at.isoformat(),
            })
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return observed_at

    def record_score(self, registration_id: str, *, result_sha256: str) -> FamilyEvaluation:
        """Bind exactly one immutable result to its registered prior and first-access clock."""
        scored_at = self._now()
        self._con.execute("BEGIN IMMEDIATE")
        try:
            events = self._events(registration_id)
            if not events or events[0][0] != "REGISTERED":
                raise FamilyRegistrationError("scoring requires a persisted registration")
            if any(kind == "SCORE" for kind, _ in events):
                raise FamilyRegistrationError("a registered family evaluation can be scored only once")
            access = next((body for kind, body in events if kind == "OUTCOME_ACCESS"), None)
            if access is None:
                raise FamilyRegistrationError("result cannot be scored before outcome access is recorded")
            registration = _registration_from_identity(events[0][1])
            evaluation = FamilyEvaluation(
                registration,
                datetime.fromisoformat(str(access["first_outcome_access_at"])),
                scored_at,
                result_sha256,
                datetime.fromisoformat(str(access["outcome_available_at"])),
            )
            record = evaluation.audit_record()
            self._append(registration_id, "SCORE", record)
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return evaluation

    def audit(self) -> dict[str, object]:
        """Verify the global hash chain and each registration/access/score lifecycle."""
        rows = self._con.execute(
            "SELECT registration_id,event_type,body_json,previous_hash,event_hash "
            "FROM family_registration_events ORDER BY sequence"
        ).fetchall()
        previous = GENESIS
        grouped: dict[str, list[tuple[str, dict[str, object]]]] = {}
        for registration_id, kind, encoded, prior, digest in rows:
            if prior != previous or hashlib.sha256((prior + encoded).encode("utf-8")).hexdigest() != digest:
                raise FamilyRegistrationError("family registration event chain is corrupt")
            grouped.setdefault(registration_id, []).append((kind, json.loads(encoded)))
            previous = digest
        for _registration_id, events in grouped.items():
            if not events or events[0][0] != "REGISTERED" or any(kind == "REGISTERED" for kind, _ in events[1:]):
                raise FamilyRegistrationError("malformed or duplicate family registration")
            kinds = [kind for kind, _ in events]
            if kinds not in (["REGISTERED"], ["REGISTERED", "OUTCOME_ACCESS"],
                             ["REGISTERED", "OUTCOME_ACCESS", "SCORE"]):
                raise FamilyRegistrationError("invalid family registration event order")
            registration = _registration_from_identity(events[0][1])
            accesses = [body for kind, body in events if kind == "OUTCOME_ACCESS"]
            scores = [body for kind, body in events if kind == "SCORE"]
            if len(accesses) > 1 or len(scores) > 1 or (scores and not accesses):
                raise FamilyRegistrationError("invalid family access/score event sequence")
            if accesses:
                digest = accesses[0].get("dataset_sha256")
                if not isinstance(digest, str) or len(digest) != 64 or any(
                    char not in "0123456789abcdef" for char in digest
                ):
                    raise FamilyRegistrationError("malformed outcome dataset identity")
                available = _utc(datetime.fromisoformat(str(accesses[0]["outcome_available_at"])),
                                 "outcome availability clock")
                accessed = _utc(datetime.fromisoformat(str(accesses[0]["first_outcome_access_at"])),
                                 "first outcome access clock")
                if available > accessed:
                    raise FamilyRegistrationError("outcome availability follows first access")
            if scores:
                score = scores[0]
                ev = FamilyEvaluation(registration,
                                      datetime.fromisoformat(str(accesses[0]["first_outcome_access_at"])),
                                      datetime.fromisoformat(str(score["scored_at"])),
                                      str(score["result_sha256"]),
                                      datetime.fromisoformat(str(accesses[0]["outcome_available_at"])))
                if score != ev.audit_record():
                    raise FamilyRegistrationError("score event does not match its registered evidence")
        return {"registrations": len(grouped), "events": len(rows), "head_sha256": previous,
                "integrity": "VALID"}


def _registration_from_identity(record: dict[str, object]) -> FamilyRegistration:
    try:
        family_size = record["family_size"]
        if type(family_size) is not int:
            raise FamilyRegistrationError("family size must be a positive integer fixed at registration")
        return FamilyRegistration(
            registration_id=str(record["registration_id"]),
            hypothesis_id=str(record["hypothesis_id"]),
            prior_class=PriorClass(str(record["prior_class"])),
            family_size=family_size,
            registered_at=datetime.fromisoformat(str(record["registered_at"])),
            preregistration_sha256=str(record["preregistration_sha256"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise FamilyRegistrationError("malformed family registration identity") from exc


__all__ = [
    "EvidenceDisposition", "FamilyEvaluation", "FamilyRegistration", "FamilyRegistrationError",
    "FamilyRegistrationLedger", "PriorClass",
]
