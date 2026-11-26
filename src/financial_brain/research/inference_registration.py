"""Freeze multiplicity/dependence inference design before the first outcome read."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Callable

GENESIS = "0" * 64


class InferenceRegistrationError(ValueError):
    """Invalid or retrospectively selected inference plan."""


class InferenceMethod(StrEnum):
    PBO_CSCV = "PBO_CSCV"
    DEFLATED_SHARPE = "DEFLATED_SHARPE"
    WHITE_REALITY_CHECK = "WHITE_REALITY_CHECK"
    HANSEN_SPA = "HANSEN_SPA"
    HOLM = "HOLM"
    SIDAK = "SIDAK"
    HAC_BLOCK_BOOTSTRAP = "HAC_BLOCK_BOOTSTRAP"


class InferenceDisposition(StrEnum):
    PRE_REGISTERED = "PRE_REGISTERED"
    RETROSPECTIVE_DIAGNOSTIC_ONLY = "RETROSPECTIVE_DIAGNOSTIC_ONLY"


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InferenceRegistrationError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class InferencePlan:
    plan_id: str
    method: InferenceMethod
    candidate_ids: tuple[str, ...]
    alpha: float
    registered_at: datetime
    preregistration_sha256: str
    block_length: float | None = None
    bootstrap_resamples: int | None = None

    def __post_init__(self) -> None:
        if not self.plan_id.strip() or not isinstance(self.method, InferenceMethod):
            raise InferenceRegistrationError("plan ID and closed inference method are required")
        if not self.candidate_ids or any(not isinstance(x, str) or not x.strip() for x in self.candidate_ids):
            raise InferenceRegistrationError("candidate family must be explicit and nonempty")
        if len(set(self.candidate_ids)) != len(self.candidate_ids) or tuple(sorted(self.candidate_ids)) != self.candidate_ids:
            raise InferenceRegistrationError("candidate IDs must be unique and frozen in sorted order")
        if self.method in {InferenceMethod.PBO_CSCV, InferenceMethod.WHITE_REALITY_CHECK, InferenceMethod.HANSEN_SPA} \
                and len(self.candidate_ids) < 2:
            raise InferenceRegistrationError("family-level inference requires at least two candidates")
        if not math.isfinite(self.alpha) or not 0 < self.alpha < 0.5:
            raise InferenceRegistrationError("alpha must be fixed in (0, 0.5)")
        _utc(self.registered_at, "registration timestamp")
        digest = self.preregistration_sha256
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise InferenceRegistrationError("preregistration must use lowercase SHA-256")
        needs_block = self.method in {InferenceMethod.WHITE_REALITY_CHECK, InferenceMethod.HANSEN_SPA,
                                      InferenceMethod.HAC_BLOCK_BOOTSTRAP}
        if needs_block and (self.block_length is None or not math.isfinite(self.block_length)
                            or self.block_length < 1):
            raise InferenceRegistrationError("selected bootstrap method needs a positive fixed block length")
        if not needs_block and self.block_length is not None:
            raise InferenceRegistrationError("block length is not applicable to the selected method")
        if self.bootstrap_resamples is not None and (
            type(self.bootstrap_resamples) is not int or self.bootstrap_resamples < 200
        ):
            raise InferenceRegistrationError("bootstrap resamples must be at least 200")
        if needs_block and self.bootstrap_resamples is None:
            raise InferenceRegistrationError("bootstrap replicate count must be fixed before outcome access")

    def sha256(self) -> str:
        payload = {"plan_id": self.plan_id, "method": self.method.value,
                   "candidate_ids": list(self.candidate_ids), "alpha": self.alpha,
                   "registered_at": _utc(self.registered_at, "registration timestamp").isoformat(),
                   "preregistration_sha256": self.preregistration_sha256,
                   "block_length": self.block_length, "bootstrap_resamples": self.bootstrap_resamples}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class InferenceUse:
    plan: InferencePlan
    first_outcome_access_at: datetime
    scored_at: datetime
    outcome_available_at: datetime | None = None

    def __post_init__(self) -> None:
        access = _utc(self.first_outcome_access_at, "first outcome access timestamp")
        scored = _utc(self.scored_at, "score timestamp")
        if scored < access:
            raise InferenceRegistrationError("score cannot precede first outcome access")
        available = _utc(self.outcome_available_at or self.first_outcome_access_at,
                         "outcome availability timestamp")
        if available > access:
            raise InferenceRegistrationError("outcome cannot be accessed before it is available")

    @property
    def disposition(self) -> InferenceDisposition:
        registered = _utc(self.plan.registered_at, "registration timestamp")
        available = _utc(self.outcome_available_at or self.first_outcome_access_at,
                         "outcome availability timestamp")
        return (InferenceDisposition.PRE_REGISTERED if registered < available
                else InferenceDisposition.RETROSPECTIVE_DIAGNOSTIC_ONLY)

    @property
    def may_certify_forward(self) -> bool:
        return self.disposition is InferenceDisposition.PRE_REGISTERED


class InferencePlanRegistry:
    """Durable local chronology for inference design, outcome availability and one score."""

    def __init__(self, path: Path | str, *, clock: Callable[[], datetime] | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS inference_plan_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            body_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL UNIQUE)""")
        self._con.execute("CREATE INDEX IF NOT EXISTS inference_plan_lookup "
                          "ON inference_plan_events(plan_id, sequence)")

    def close(self) -> None:
        self._con.close()

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise InferenceRegistrationError("registry clock must return a datetime")
        return _utc(value, "registry clock")

    def _append(self, plan_id: str, event_type: str, body: dict[str, object]) -> str:
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        tail = self._con.execute(
            "SELECT event_hash FROM inference_plan_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous = tail[0] if tail else GENESIS
        digest = hashlib.sha256((previous + encoded).encode("utf-8")).hexdigest()
        self._con.execute("INSERT INTO inference_plan_events "
                          "(plan_id,event_type,body_json,previous_hash,event_hash) VALUES (?,?,?,?,?)",
                          (plan_id, event_type, encoded, previous, digest))
        return digest

    def _events(self, plan_id: str) -> list[tuple[str, dict[str, object]]]:
        rows = self._con.execute(
            "SELECT event_type,body_json FROM inference_plan_events WHERE plan_id=? ORDER BY sequence",
            (plan_id,),
        ).fetchall()
        return [(kind, json.loads(body)) for kind, body in rows]

    def register(self, *, method: InferenceMethod, candidate_ids: tuple[str, ...], alpha: float,
                 preregistration_sha256: str, block_length: float | None = None,
                 bootstrap_resamples: int | None = None) -> InferencePlan:
        """Freeze the inference family/method with a locally assigned registration clock."""
        plan = InferencePlan(uuid.uuid4().hex, method, candidate_ids, alpha, self._now(),
                             preregistration_sha256, block_length, bootstrap_resamples)
        self._con.execute("BEGIN IMMEDIATE")
        try:
            if self._con.execute("SELECT 1 FROM inference_plan_events WHERE plan_id=? LIMIT 1",
                                 (plan.plan_id,)).fetchone():
                raise InferenceRegistrationError("inference plan ID collision")
            self._append(plan.plan_id, "REGISTERED", {
                "plan_id": plan.plan_id, "method": plan.method.value,
                "candidate_ids": list(plan.candidate_ids), "alpha": plan.alpha,
                "registered_at": _utc(plan.registered_at, "registration timestamp").isoformat(),
                "preregistration_sha256": plan.preregistration_sha256,
                "block_length": plan.block_length, "bootstrap_resamples": plan.bootstrap_resamples,
            })
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return plan

    def record_outcome_access(self, plan_id: str, *, dataset_sha256: str,
                              outcome_available_at: datetime) -> datetime:
        if (not isinstance(dataset_sha256, str) or len(dataset_sha256) != 64
                or any(c not in "0123456789abcdef" for c in dataset_sha256)):
            raise InferenceRegistrationError("dataset identity must be lowercase SHA-256")
        available = _utc(outcome_available_at, "outcome availability timestamp")
        accessed = self._now()
        if available > accessed:
            raise InferenceRegistrationError("outcome availability cannot follow first access")
        self._con.execute("BEGIN IMMEDIATE")
        try:
            events = self._events(plan_id)
            if not events or events[0][0] != "REGISTERED":
                raise InferenceRegistrationError("outcome access requires a persisted inference plan")
            if any(kind == "OUTCOME_ACCESS" for kind, _ in events):
                raise InferenceRegistrationError("first outcome access is already recorded")
            self._append(plan_id, "OUTCOME_ACCESS", {
                "dataset_sha256": dataset_sha256,
                "outcome_available_at": available.isoformat(),
                "first_outcome_access_at": accessed.isoformat(),
            })
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return accessed

    def record_result(self, plan_id: str, *, result_sha256: str) -> InferenceUse:
        if (not isinstance(result_sha256, str) or len(result_sha256) != 64
                or any(c not in "0123456789abcdef" for c in result_sha256)):
            raise InferenceRegistrationError("result identity must be lowercase SHA-256")
        scored = self._now()
        self._con.execute("BEGIN IMMEDIATE")
        try:
            events = self._events(plan_id)
            if not events or events[0][0] != "REGISTERED":
                raise InferenceRegistrationError("result requires a persisted inference plan")
            if any(kind == "RESULT" for kind, _ in events):
                raise InferenceRegistrationError("an inference plan can produce only one scored result")
            access = next((body for kind, body in events if kind == "OUTCOME_ACCESS"), None)
            if access is None:
                raise InferenceRegistrationError("result cannot be recorded before outcome access")
            plan = _plan_from_record(events[0][1])
            use = InferenceUse(plan, datetime.fromisoformat(str(access["first_outcome_access_at"])),
                               scored, datetime.fromisoformat(str(access["outcome_available_at"])))
            body: dict[str, object] = {"result_sha256": result_sha256,
                    "plan_sha256": plan.sha256(),
                    "outcome_available_at": (use.outcome_available_at or use.first_outcome_access_at).isoformat(),
                    "first_outcome_access_at": use.first_outcome_access_at.isoformat(),
                    "scored_at": _utc(scored, "score timestamp").isoformat(),
                    "disposition": use.disposition.value,
                    "may_certify_forward": use.may_certify_forward}
            self._append(plan_id, "RESULT", body)
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return use

    def audit(self) -> dict[str, object]:
        rows = self._con.execute(
            "SELECT plan_id,event_type,body_json,previous_hash,event_hash "
            "FROM inference_plan_events ORDER BY sequence"
        ).fetchall()
        previous = GENESIS
        grouped: dict[str, list[tuple[str, dict[str, object]]]] = {}
        for plan_id, kind, encoded, prior, digest in rows:
            if prior != previous or hashlib.sha256((prior + encoded).encode("utf-8")).hexdigest() != digest:
                raise InferenceRegistrationError("inference plan event chain is corrupt")
            grouped.setdefault(plan_id, []).append((kind, json.loads(encoded)))
            previous = digest
        for events in grouped.values():
            kinds = [kind for kind, _ in events]
            if kinds not in (["REGISTERED"], ["REGISTERED", "OUTCOME_ACCESS"],
                             ["REGISTERED", "OUTCOME_ACCESS", "RESULT"]):
                raise InferenceRegistrationError("invalid inference plan event order")
            plan = _plan_from_record(events[0][1])
            if len(events) > 1:
                access = events[1][1]
                available = _utc(datetime.fromisoformat(str(access["outcome_available_at"])),
                                 "outcome availability timestamp")
                accessed = _utc(datetime.fromisoformat(str(access["first_outcome_access_at"])),
                                "first outcome access timestamp")
                digest = access.get("dataset_sha256")
                if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise InferenceRegistrationError("malformed inference dataset identity")
                if available > accessed:
                    raise InferenceRegistrationError("outcome availability follows first access")
                if len(events) == 3:
                    result = events[2][1]
                    use = InferenceUse(plan, accessed,
                                       datetime.fromisoformat(str(result["scored_at"])), available)
                    expected = {"result_sha256": result.get("result_sha256"),
                                "plan_sha256": plan.sha256(),
                                "outcome_available_at": available.isoformat(),
                                "first_outcome_access_at": accessed.isoformat(),
                                "scored_at": _utc(use.scored_at, "score timestamp").isoformat(),
                                "disposition": use.disposition.value,
                                "may_certify_forward": use.may_certify_forward}
                    if result != expected:
                        raise InferenceRegistrationError("result does not match its frozen inference plan")
        return {"plans": len(grouped), "events": len(rows), "head_sha256": previous,
                "integrity": "VALID"}


def _plan_from_record(record: dict[str, object]) -> InferencePlan:
    try:
        candidates = record["candidate_ids"]
        if not isinstance(candidates, list) or any(not isinstance(candidate, str) for candidate in candidates):
            raise InferenceRegistrationError("malformed candidate family in stored inference plan")
        alpha = record["alpha"]
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
            raise InferenceRegistrationError("malformed alpha in stored inference plan")
        block = record["block_length"]
        if block is not None and (isinstance(block, bool) or not isinstance(block, (int, float))):
            raise InferenceRegistrationError("malformed block length in stored inference plan")
        resamples = record["bootstrap_resamples"]
        if resamples is not None and type(resamples) is not int:
            raise InferenceRegistrationError("malformed resample count in stored inference plan")
        return InferencePlan(str(record["plan_id"]), InferenceMethod(str(record["method"])),
                             tuple(candidates), float(alpha),
                             datetime.fromisoformat(str(record["registered_at"])),
                             str(record["preregistration_sha256"]),
                             float(block) if block is not None else None, resamples)
    except (KeyError, TypeError, ValueError) as exc:
        raise InferenceRegistrationError("malformed stored inference plan") from exc


__all__ = ["InferenceDisposition", "InferenceMethod", "InferencePlan", "InferencePlanRegistry",
           "InferenceRegistrationError", "InferenceUse"]
