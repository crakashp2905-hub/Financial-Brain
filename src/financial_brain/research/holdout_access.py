"""Durable one-time sealed-holdout access authorization (local tamper evidence only)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from financial_brain.forward_edge.approvals import Approval, ApprovalError, validate_approval

GENESIS = "0" * 64


class HoldoutAccessError(PermissionError):
    """Holdout access is invalid, unauthorized, already consumed, or ledger-corrupt."""


def _sha(value: str, label: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise HoldoutAccessError(f"{label} must be lowercase SHA-256")


def holdout_scope_sha256(*, dataset_id: str, dataset_sha256: str, run_id: str,
                         preregistration_sha256: str) -> str:
    if not all(isinstance(x, str) and x.strip() for x in (dataset_id, run_id)):
        raise HoldoutAccessError("dataset and run IDs are required")
    _sha(dataset_sha256, "dataset identity")
    _sha(preregistration_sha256, "preregistration")
    body = {"dataset_id": dataset_id, "dataset_sha256": dataset_sha256,
            "run_id": run_id, "preregistration_sha256": preregistration_sha256}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class HoldoutReceipt:
    dataset_id: str
    dataset_sha256: str
    run_id: str
    preregistration_sha256: str
    owner_id: str
    approved_at_utc: str
    accessed_at_utc: str
    approval_scope_sha256: str
    previous_hash: str
    event_hash: str


class HoldoutAccessLedger:
    """SQLite-backed, unique dataset/run consumption with a tamper-evident hash chain.

    This is not independent custody or cryptographic owner authentication. The sealed-data
    adapter must call ``consume`` before returning any holdout rows, and the ledger file must
    be protected by the deployment's filesystem and backup policy.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS holdout_access (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_id TEXT UNIQUE NOT NULL,
            run_id TEXT UNIQUE NOT NULL,
            body_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL UNIQUE)""")

    def close(self) -> None:
        self._con.close()

    def audit(self) -> dict[str, object]:
        rows = self._con.execute("SELECT body_json,previous_hash,event_hash FROM holdout_access ORDER BY sequence").fetchall()
        previous = GENESIS
        seen_datasets: set[str] = set()
        seen_runs: set[str] = set()
        for body, prior, digest in rows:
            if prior != previous or hashlib.sha256((prior + body).encode()).hexdigest() != digest:
                raise HoldoutAccessError("holdout access ledger hash chain is corrupt")
            record = json.loads(body)
            dataset_id, run_id = record.get("dataset_id"), record.get("run_id")
            if not dataset_id or not run_id or dataset_id in seen_datasets or run_id in seen_runs:
                raise HoldoutAccessError("holdout dataset/run reuse or malformed event detected")
            seen_datasets.add(dataset_id)
            seen_runs.add(run_id)
            previous = digest
        return {"consumed_datasets": len(rows), "head_sha256": previous, "integrity": "VALID"}

    def consume(self, *, dataset_id: str, dataset_sha256: str, run_id: str,
                preregistration_sha256: str, authorized_owner_id: str,
                approval: Approval, now: datetime) -> HoldoutReceipt:
        """Validate exact owner approval and atomically consume the only access for this dataset/run."""
        scope = holdout_scope_sha256(dataset_id=dataset_id, dataset_sha256=dataset_sha256,
                                     run_id=run_id, preregistration_sha256=preregistration_sha256)
        if not authorized_owner_id.strip():
            raise HoldoutAccessError("configured owner identity is required")
        if approval.actor_id != authorized_owner_id:
            raise HoldoutAccessError("holdout access must be approved by the configured owner")
        try:
            validate_approval(approval, kind="HOLDOUT_ACCESS", subject=run_id,
                              scope_sha256=scope, now=now)
        except ApprovalError as exc:
            raise HoldoutAccessError(f"holdout approval rejected: {exc}") from exc
        if now.tzinfo is None or now.utcoffset() is None:
            raise HoldoutAccessError("access timestamp must be timezone-aware")
        self.audit()
        self._con.execute("BEGIN IMMEDIATE")
        try:
            prior = self._con.execute("SELECT event_hash FROM holdout_access ORDER BY sequence DESC LIMIT 1").fetchone()
            previous = prior[0] if prior else GENESIS
            body = {
                "dataset_id": dataset_id, "dataset_sha256": dataset_sha256, "run_id": run_id,
                "preregistration_sha256": preregistration_sha256, "owner_id": authorized_owner_id,
                "approved_at_utc": approval.approved_at_utc.astimezone(timezone.utc).isoformat(),
                "accessed_at_utc": now.astimezone(timezone.utc).isoformat(),
                "approval_scope_sha256": scope,
            }
            canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
            digest = hashlib.sha256((previous + canonical).encode()).hexdigest()
            try:
                self._con.execute("INSERT INTO holdout_access "
                                   "(dataset_id,run_id,body_json,previous_hash,event_hash) VALUES (?,?,?,?,?)",
                                   (dataset_id, run_id, canonical, previous, digest))
            except sqlite3.IntegrityError as exc:
                raise HoldoutAccessError("sealed dataset or run ID has already been consumed") from exc
            self._con.execute("COMMIT")
            return HoldoutReceipt(dataset_id, dataset_sha256, run_id, preregistration_sha256,
                                  authorized_owner_id, body["approved_at_utc"], body["accessed_at_utc"],
                                  scope, previous, digest)
        except Exception:
            self._con.execute("ROLLBACK")
            raise


__all__ = ["HoldoutAccessError", "HoldoutAccessLedger", "HoldoutReceipt", "holdout_scope_sha256"]
