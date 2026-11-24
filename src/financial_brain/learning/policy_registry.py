"""Append-only policy artifact and promotion-evidence registry.

This registry records evidence states only. REVIEW_ELIGIBLE is not a release,
broker permission, or authorization to trade live capital.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from pathlib import Path
from typing import Mapping

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STATUSES = frozenset({"CANDIDATE", "OOS_PASSED", "SHADOW", "FORWARD_UNPROVEN",
                       "REVIEW_ELIGIBLE", "RETIRED", "BLOCKED"})
_TRANSITIONS = {
    "CANDIDATE": frozenset({"OOS_PASSED", "BLOCKED"}),
    "OOS_PASSED": frozenset({"SHADOW", "BLOCKED"}),
    "SHADOW": frozenset({"FORWARD_UNPROVEN", "BLOCKED", "RETIRED"}),
    "FORWARD_UNPROVEN": frozenset({"REVIEW_ELIGIBLE", "BLOCKED", "RETIRED"}),
    "REVIEW_ELIGIBLE": frozenset({"BLOCKED", "RETIRED"}),
    "BLOCKED": frozenset(),
    "RETIRED": frozenset(),
}


class PolicyRegistryError(ValueError):
    """Policy artifact or evidence transition is invalid."""


def _hash(value: str, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise PolicyRegistryError(f"{field} must be a lowercase SHA-256")
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PolicyRegistryError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class PolicyArtifact:
    policy_id: str
    code_sha256: str
    weights_sha256: str
    reward_definition_sha256: str
    training_data_sha256: str
    feature_versions: Mapping[str, str]
    risk_caps_sha256: str
    source_rights_sha256: str
    evaluation_protocol_sha256: str

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise PolicyRegistryError("policy_id is required")
        for field in ("code_sha256", "weights_sha256", "reward_definition_sha256",
                      "training_data_sha256", "risk_caps_sha256", "source_rights_sha256",
                      "evaluation_protocol_sha256"):
            _hash(getattr(self, field), field)
        if not self.feature_versions or any(
            not isinstance(name, str) or not name.strip() or
            not isinstance(version, str) or not version.strip()
            for name, version in self.feature_versions.items()
        ):
            raise PolicyRegistryError("feature names and versions are required")
        object.__setattr__(self, "feature_versions",
                           MappingProxyType(dict(sorted(self.feature_versions.items()))))

    def body(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "code_sha256": self.code_sha256,
            "weights_sha256": self.weights_sha256,
            "reward_definition_sha256": self.reward_definition_sha256,
            "training_data_sha256": self.training_data_sha256,
            "feature_versions": dict(sorted(self.feature_versions.items())),
            "risk_caps_sha256": self.risk_caps_sha256,
            "source_rights_sha256": self.source_rights_sha256,
            "evaluation_protocol_sha256": self.evaluation_protocol_sha256,
        }

    def digest(self) -> str:
        encoded = json.dumps(self.body(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode()).hexdigest()


class PolicyRegistry:
    """Durable append-only policy states with a hash-chained event history."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS policy_artifacts (
            policy_id TEXT PRIMARY KEY, artifact_sha256 TEXT NOT NULL UNIQUE, artifact_json TEXT NOT NULL)""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS policy_events (
            sequence INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, policy_id TEXT NOT NULL,
            status TEXT NOT NULL, actor_id TEXT NOT NULL, actor_type TEXT NOT NULL,
            evidence_sha256 TEXT NOT NULL, approval_sha256 TEXT, occurred_at TEXT NOT NULL,
            previous_event_sha256 TEXT NOT NULL, event_sha256 TEXT NOT NULL, event_json TEXT NOT NULL,
            FOREIGN KEY(policy_id) REFERENCES policy_artifacts(policy_id))""")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS policy_artifacts_no_update BEFORE UPDATE ON policy_artifacts BEGIN SELECT RAISE(ABORT,'policy artifacts are immutable'); END")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS policy_artifacts_no_delete BEFORE DELETE ON policy_artifacts BEGIN SELECT RAISE(ABORT,'policy artifacts are immutable'); END")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS policy_events_no_update BEFORE UPDATE ON policy_events BEGIN SELECT RAISE(ABORT,'policy events are append-only'); END")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS policy_events_no_delete BEFORE DELETE ON policy_events BEGIN SELECT RAISE(ABORT,'policy events are append-only'); END")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def register(self, artifact: PolicyArtifact, *, event_id: str, actor_id: str, reason: str,
                 actor_type: str, evidence_sha256: str, occurred_at: datetime) -> bool:
        """Register exactly one immutable artifact in CANDIDATE state."""
        if not all(value.strip() for value in (event_id, actor_id, reason)):
            raise PolicyRegistryError("event_id, actor_id and reason are required")
        if actor_type not in {"RESEARCHER", "GENERATOR"}:
            raise PolicyRegistryError("candidate registration actor type is unsupported")
        _hash(evidence_sha256, "evidence_sha256")
        at = _utc(occurred_at)
        body = artifact.body()
        artifact_json = _canonical(body)
        digest = artifact.digest()
        return self._append_event(policy_id=artifact.policy_id, status="CANDIDATE", event_id=event_id,
                                  actor_id=actor_id, actor_type=actor_type,
                                  reason=reason, evidence_sha256=evidence_sha256, approval_sha256=None,
                                  occurred_at=at, artifact=(digest, artifact_json))

    def transition(self, policy_id: str, *, status: str, event_id: str, actor_id: str, reason: str,
                   actor_type: str, evidence_sha256: str, occurred_at: datetime,
                   approval_sha256: str | None = None) -> bool:
        if status not in _STATUSES or status == "CANDIDATE":
            raise PolicyRegistryError("transition target status is invalid")
        if actor_type not in {"EVALUATOR", "HUMAN_REVIEWER", "SYSTEM_SAFETY"}:
            raise PolicyRegistryError("candidate generators cannot author promotion evidence")
        if status == "REVIEW_ELIGIBLE":
            if actor_type != "HUMAN_REVIEWER" or approval_sha256 is None:
                raise PolicyRegistryError("review eligibility requires a human reviewer and approval receipt")
            _hash(approval_sha256, "approval_sha256")
        elif approval_sha256 is not None:
            _hash(approval_sha256, "approval_sha256")
        if actor_type == "SYSTEM_SAFETY" and status not in {"BLOCKED", "RETIRED"}:
            raise PolicyRegistryError("safety actor may only block or retire a policy")
        _hash(evidence_sha256, "evidence_sha256")
        if not all(value.strip() for value in (policy_id, event_id, actor_id, reason)):
            raise PolicyRegistryError("policy, event, actor and reason are required")
        at = _utc(occurred_at)
        return self._append_event(policy_id=policy_id, status=status, event_id=event_id,
                                  actor_id=actor_id, actor_type=actor_type,
                                  reason=reason, evidence_sha256=evidence_sha256,
                                  approval_sha256=approval_sha256,
                                  occurred_at=at, artifact=None)

    def status(self, policy_id: str) -> str:
        row = self._con.execute(
            "SELECT status FROM policy_events WHERE policy_id=? ORDER BY sequence DESC LIMIT 1",
            [policy_id],
        ).fetchone()
        if row is None:
            raise PolicyRegistryError("unknown policy")
        return str(row[0])

    def artifact(self, policy_id: str) -> dict[str, object]:
        row = self._con.execute("SELECT artifact_json FROM policy_artifacts WHERE policy_id=?",
                                [policy_id]).fetchone()
        if row is None:
            raise PolicyRegistryError("unknown policy")
        return json.loads(row[0])

    def events(self, policy_id: str | None = None) -> tuple[dict[str, object], ...]:
        sql = "SELECT sequence,event_json FROM policy_events"
        params: tuple[object, ...] = ()
        if policy_id is not None:
            sql += " WHERE policy_id=?"
            params = (policy_id,)
        sql += " ORDER BY sequence"
        return tuple({"sequence": int(sequence), **json.loads(body)}
                     for sequence, body in self._con.execute(sql, params).fetchall())

    def verify(self) -> bool:
        previous = "0" * 64
        rows = self._con.execute(
            """SELECT sequence,event_id,policy_id,status,actor_id,actor_type,evidence_sha256,
                      approval_sha256,occurred_at,previous_event_sha256,event_sha256,event_json
               FROM policy_events ORDER BY sequence"""
        ).fetchall()
        for expected, (sequence,event_id,policy_id,status,actor_id,actor_type,evidence_sha256,
                       approval_sha256,occurred_at,prior,digest,body) in enumerate(rows, start=1):
            if sequence != expected or prior != previous:
                return False
            if hashlib.sha256(body.encode()).hexdigest() != digest:
                return False
            try:
                event = json.loads(body)
            except (TypeError, json.JSONDecodeError):
                return False
            artifact = self._con.execute(
                "SELECT artifact_sha256,artifact_json FROM policy_artifacts WHERE policy_id=?", [policy_id]
            ).fetchone()
            if artifact is None:
                return False
            artifact_digest, artifact_json = artifact
            if hashlib.sha256(artifact_json.encode()).hexdigest() != artifact_digest:
                return False
            expected_event = {"event_id": event_id, "policy_id": policy_id, "status": status,
                              "actor_id": actor_id, "actor_type": actor_type,
                              "evidence_sha256": evidence_sha256, "approval_sha256": approval_sha256,
                              "occurred_at": occurred_at, "previous_event_sha256": prior,
                              "artifact_sha256": artifact_digest}
            if (not isinstance(event, dict) or set(event) != set(expected_event) | {"reason"}
                    or any(event.get(key) != value for key, value in expected_event.items())
                    or not isinstance(event.get("reason"), str) or not event["reason"].strip()):
                return False
            previous = digest
        return True

    def _append_event(self, *, policy_id: str, status: str, event_id: str, actor_id: str,
                      actor_type: str, reason: str, evidence_sha256: str, approval_sha256: str | None,
                      occurred_at: datetime, artifact: tuple[str, str] | None) -> bool:
        self._con.execute("BEGIN IMMEDIATE")
        try:
            if artifact is not None:
                existing = self._con.execute(
                    "SELECT artifact_sha256,artifact_json FROM policy_artifacts WHERE policy_id=?",
                    [policy_id],
                ).fetchone()
                if existing is not None:
                    if existing == artifact:
                        self._con.rollback()
                        return False
                    raise PolicyRegistryError("policy_id is already bound to a different immutable artifact")
                self._con.execute("INSERT INTO policy_artifacts VALUES (?,?,?)",
                                  [policy_id, artifact[0], artifact[1]])
                artifact_digest = artifact[0]
            else:
                existing = self._con.execute(
                    "SELECT artifact_sha256 FROM policy_artifacts WHERE policy_id=?", [policy_id]
                ).fetchone()
                if existing is None:
                    raise PolicyRegistryError("unknown policy")
                artifact_digest = str(existing[0])
                current_row = self._con.execute(
                    "SELECT status FROM policy_events WHERE policy_id=? ORDER BY sequence DESC LIMIT 1",
                    [policy_id],
                ).fetchone()
                if current_row is None:
                    raise PolicyRegistryError("policy has no candidate event")
                current = str(current_row[0])
                if status not in _TRANSITIONS[current]:
                    raise PolicyRegistryError(f"transition {current} -> {status} is not allowed")
            previous_row = self._con.execute(
                "SELECT event_sha256 FROM policy_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous = str(previous_row[0]) if previous_row else "0" * 64
            event = {"event_id": event_id, "policy_id": policy_id, "status": status,
                     "actor_id": actor_id, "actor_type": actor_type, "reason": reason,
                     "evidence_sha256": evidence_sha256,
                     "approval_sha256": approval_sha256, "occurred_at": occurred_at.isoformat(),
                     "previous_event_sha256": previous, "artifact_sha256": artifact_digest}
            encoded = _canonical(event)
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            sequence = int(self._con.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM policy_events").fetchone()[0])
            self._con.execute("""INSERT INTO policy_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                              [sequence, event_id, policy_id, status, actor_id, actor_type,
                               evidence_sha256, approval_sha256, occurred_at.isoformat(), previous, digest, encoded])
            self._con.commit()
        except Exception:
            self._con.rollback()
            raise
        return True


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


__all__ = ["PolicyArtifact", "PolicyRegistry", "PolicyRegistryError"]
