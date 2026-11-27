"""SQLite-backed account reservation gate for paper desks.

The immediate write transaction makes each reservation atomic across all resource
limits and concurrent desk processes. It does not replace fills or the canonical book:
only a source-bound terminal event releases an order reservation.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


_HASH = re.compile(r"^[0-9a-f]{64}$")
_ZERO = Decimal("0")


class ReservationError(ValueError):
    """Invalid, conflicting, or over-budget reservation request."""


class ReservationDisposition(StrEnum):
    RESERVED = "RESERVED"
    REPLAYED = "REPLAYED"
    RELEASED = "RELEASED"
    ALREADY_RELEASED = "ALREADY_RELEASED"


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReservationError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _decimal(value: Decimal | str | int, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ReservationError(f"{field} must be a finite non-negative decimal") from exc
    if not result.is_finite() or result < 0:
        raise ReservationError(f"{field} must be a finite non-negative decimal")
    return result


def _hash(value: str, field: str) -> None:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise ReservationError(f"{field} must be a lowercase SHA-256")


def _digest(value: object) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False, default=str).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


@dataclass(frozen=True, slots=True)
class ReservationPolicy:
    account_id: str
    version: str
    frozen_at: datetime
    limits: Mapping[str, Decimal]
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.account_id.strip() or not self.version.strip():
            raise ReservationError("account ID and policy version are required")
        frozen = _utc(self.frozen_at, "frozen_at")
        _hash(self.source_sha256, "policy source hash")
        limits: dict[str, Decimal] = {}
        for resource, amount in self.limits.items():
            key = str(resource).strip()
            if not key or key in limits:
                raise ReservationError("resource limit keys must be non-empty and unique")
            limits[key] = _decimal(amount, f"limit {key}")
        if not limits:
            raise ReservationError("at least one resource limit is required")
        object.__setattr__(self, "frozen_at", frozen)
        object.__setattr__(self, "limits", MappingProxyType(dict(sorted(limits.items()))))

    def digest(self) -> str:
        return _digest({"account_id": self.account_id, "version": self.version,
                        "frozen_at": self.frozen_at.isoformat(),
                        "limits": {key: str(value) for key, value in self.limits.items()},
                        "source_sha256": self.source_sha256})


@dataclass(frozen=True, slots=True)
class ReservationRequest:
    reservation_id: str
    account_id: str
    desk_id: str
    created_at: datetime
    amounts: Mapping[str, Decimal]
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.reservation_id.strip() or not self.account_id.strip() or not self.desk_id.strip():
            raise ReservationError("reservation, account and desk IDs are required")
        created = _utc(self.created_at, "created_at")
        _hash(self.source_sha256, "request source hash")
        amounts: dict[str, Decimal] = {}
        for resource, amount in self.amounts.items():
            key = str(resource).strip()
            if not key or key in amounts:
                raise ReservationError("reservation resource keys must be non-empty and unique")
            parsed = _decimal(amount, f"amount {key}")
            if parsed == 0:
                raise ReservationError("zero-value resources must be omitted")
            amounts[key] = parsed
        if not amounts:
            raise ReservationError("reservation must reserve at least one resource")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "amounts", MappingProxyType(dict(sorted(amounts.items()))))

    def body(self) -> dict[str, object]:
        return {"reservation_id": self.reservation_id, "account_id": self.account_id,
                "desk_id": self.desk_id, "created_at": self.created_at.isoformat(),
                "amounts": {key: str(value) for key, value in self.amounts.items()},
                "source_sha256": self.source_sha256}


@dataclass(frozen=True, slots=True)
class ReservationResult:
    disposition: ReservationDisposition
    reservation_id: str
    account_id: str
    policy_sha256: str
    request_sha256: str
    active_amounts: Mapping[str, Decimal]
    limits: Mapping[str, Decimal]
    event_sha256: str


class DurableReservationLedger:
    """Durable cross-process reservation store with immutable event receipts."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS reservation_policies (
                    account_id TEXT PRIMARY KEY,
                    policy_sha256 TEXT NOT NULL,
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reservations (
                    account_id TEXT NOT NULL,
                    reservation_id TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('ACTIVE','RELEASED')),
                    PRIMARY KEY(account_id, reservation_id)
                );
                CREATE TABLE IF NOT EXISTS reservation_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    reservation_id TEXT NOT NULL,
                    event_kind TEXT NOT NULL,
                    event_sha256 TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    UNIQUE(account_id, event_id)
                );
                CREATE INDEX IF NOT EXISTS active_reservations_by_account
                    ON reservations(account_id, state);
            """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _connection(self):
        return _Connection(self._connect())

    def reserve(self, policy: ReservationPolicy, request: ReservationRequest) -> ReservationResult:
        """Atomically reserve all requested resources or reserve none of them."""
        if request.account_id != policy.account_id or request.created_at <= policy.frozen_at:
            raise ReservationError("request account and time must match the frozen policy scope")
        unknown = sorted(set(request.amounts) - set(policy.limits))
        if unknown:
            raise ReservationError(f"resources have no frozen policy limits: {unknown}")
        policy_sha = policy.digest()
        request_sha = _digest(request.body())
        policy_body = json.dumps({"version": policy.version, "frozen_at": policy.frozen_at.isoformat(),
                                  "limits": {key: str(value) for key, value in policy.limits.items()},
                                  "source_sha256": policy.source_sha256},
                                 sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_policy = connection.execute(
                "SELECT policy_sha256 FROM reservation_policies WHERE account_id=?",
                (policy.account_id,),
            ).fetchone()
            if existing_policy is None:
                connection.execute("INSERT INTO reservation_policies VALUES (?,?,?)",
                                   (policy.account_id, policy_sha, policy_body))
            elif existing_policy["policy_sha256"] != policy_sha:
                raise ReservationError("reservation policy changed; explicit migration is required")

            previous = connection.execute(
                "SELECT request_sha256,state FROM reservations WHERE account_id=? AND reservation_id=?",
                (request.account_id, request.reservation_id),
            ).fetchone()
            if previous is not None:
                if previous["request_sha256"] != request_sha:
                    raise ReservationError("reservation ID was replayed with different request content")
                if previous["state"] != "ACTIVE":
                    raise ReservationError("released reservation cannot be replayed")
                return self._result(connection, request, policy, ReservationDisposition.REPLAYED,
                                    request_sha, policy_sha)

            used: dict[str, Decimal] = {resource: _ZERO for resource in policy.limits}
            rows = connection.execute(
                "SELECT request_json FROM reservations WHERE account_id=? AND state='ACTIVE'",
                (request.account_id,),
            ).fetchall()
            for row in rows:
                saved = json.loads(row["request_json"])
                for resource, amount in saved["amounts"].items():
                    used[resource] += Decimal(amount)
            projected = {resource: used[resource] + request.amounts.get(resource, _ZERO)
                         for resource in policy.limits}
            exceeded = {resource: (projected[resource], policy.limits[resource])
                        for resource in policy.limits if projected[resource] > policy.limits[resource]}
            if exceeded:
                raise ReservationError(f"account resource budget exceeded: {exceeded}")

            body = json.dumps(request.body(), sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False, allow_nan=False)
            connection.execute("INSERT INTO reservations VALUES (?,?,?,?, 'ACTIVE')",
                               (request.account_id, request.reservation_id, request_sha, body))
            event = {"account_id": request.account_id, "event_id": f"RESERVE:{request.reservation_id}",
                     "reservation_id": request.reservation_id, "event_kind": "RESERVED",
                     "request_sha256": request_sha, "policy_sha256": policy_sha,
                     "occurred_at": request.created_at.isoformat(),
                     "source_sha256": request.source_sha256}
            self._append_event(connection, event)
            return self._result(connection, request, policy, ReservationDisposition.RESERVED,
                                request_sha, policy_sha)

    def release(self, *, account_id: str, reservation_id: str, terminal_event_id: str,
                terminal_kind: str, occurred_at: datetime, source_sha256: str) -> ReservationDisposition:
        """Release only against a source-bound terminal OMS/paper event; never infer a fill."""
        if not account_id.strip() or not reservation_id.strip() or not terminal_event_id.strip():
            raise ReservationError("account, reservation and terminal event IDs are required")
        if terminal_kind not in {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}:
            raise ReservationError("only a terminal order state can release a reservation")
        when = _utc(occurred_at, "occurred_at")
        _hash(source_sha256, "terminal event source hash")
        event_id = f"RELEASE:{reservation_id}:{terminal_event_id}"
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state FROM reservations WHERE account_id=? AND reservation_id=?",
                (account_id, reservation_id),
            ).fetchone()
            if row is None:
                raise ReservationError("unknown reservation cannot be released")
            if row["state"] == "RELEASED":
                event = {"account_id": account_id, "event_id": event_id,
                         "reservation_id": reservation_id, "event_kind": "RELEASED",
                         "terminal_kind": terminal_kind, "occurred_at": when.isoformat(),
                         "source_sha256": source_sha256}
                expected_sha = _digest(event)
                prior = connection.execute(
                    "SELECT event_sha256 FROM reservation_events WHERE account_id=? AND event_id=?",
                    (account_id, event_id),
                ).fetchone()
                if prior is None:
                    raise ReservationError("reservation is released without matching terminal evidence")
                if prior["event_sha256"] != expected_sha:
                    raise ReservationError("terminal event ID was replayed with conflicting content")
                return ReservationDisposition.ALREADY_RELEASED
            event = {"account_id": account_id, "event_id": event_id,
                     "reservation_id": reservation_id, "event_kind": "RELEASED",
                     "terminal_kind": terminal_kind, "occurred_at": when.isoformat(),
                     "source_sha256": source_sha256}
            self._append_event(connection, event)
            connection.execute("UPDATE reservations SET state='RELEASED' WHERE account_id=? AND reservation_id=?",
                               (account_id, reservation_id))
            return ReservationDisposition.RELEASED

    def active_amounts(self, account_id: str) -> Mapping[str, Decimal]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT request_json FROM reservations WHERE account_id=? AND state='ACTIVE'",
                (account_id,),
            ).fetchall()
        totals: dict[str, Decimal] = {}
        for row in rows:
            request = json.loads(row["request_json"])
            for resource, amount in request["amounts"].items():
                totals[resource] = totals.get(resource, _ZERO) + Decimal(amount)
        return MappingProxyType(dict(sorted(totals.items())))

    def verify(self, account_id: str) -> dict[str, int]:
        """Verify stored event JSON hashes and reservation state/event linkage."""
        with self._connection() as connection:
            events = connection.execute(
                "SELECT event_id,event_sha256,event_json FROM reservation_events WHERE account_id=? ORDER BY sequence",
                (account_id,),
            ).fetchall()
            reservations = connection.execute(
                "SELECT reservation_id,state FROM reservations WHERE account_id=?", (account_id,),
            ).fetchall()
        mismatches = sum(1 for row in events if _digest(json.loads(row["event_json"])) != row["event_sha256"])
        event_ids = {row["event_id"] for row in events}
        missing = sum(1 for row in reservations
                      if f"RESERVE:{row['reservation_id']}" not in event_ids)
        release_missing = sum(1 for row in reservations if row["state"] == "RELEASED"
                              and not any(event_id.startswith(f"RELEASE:{row['reservation_id']}:")
                                          for event_id in event_ids))
        return {"events": len(events), "mismatched": mismatches,
                "reservations_missing_receipts": missing,
                "released_reservations_missing_receipts": release_missing}

    def _append_event(self, connection: sqlite3.Connection, body: Mapping[str, object]) -> str:
        event_sha = _digest(body)
        connection.execute("INSERT INTO reservation_events(account_id,event_id,reservation_id,event_kind,event_sha256,event_json) VALUES(?,?,?,?,?,?)",
                           (body["account_id"], body["event_id"], body["reservation_id"],
                            body["event_kind"], event_sha,
                            json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                       allow_nan=False)))
        return event_sha

    def _result(self, connection: sqlite3.Connection, request: ReservationRequest,
                policy: ReservationPolicy, disposition: ReservationDisposition,
                request_sha: str, policy_sha: str) -> ReservationResult:
        active: dict[str, Decimal] = {}
        rows = connection.execute(
            "SELECT request_json FROM reservations WHERE account_id=? AND state='ACTIVE'",
            (request.account_id,),
        ).fetchall()
        for active_row in rows:
            saved = json.loads(active_row["request_json"])
            for resource, amount in saved["amounts"].items():
                active[resource] = active.get(resource, _ZERO) + Decimal(amount)
        row = connection.execute(
            "SELECT event_sha256 FROM reservation_events WHERE account_id=? AND event_id=?",
            (request.account_id, f"RESERVE:{request.reservation_id}"),
        ).fetchone()
        if row is None:
            raise ReservationError("reservation event receipt is missing")
        return ReservationResult(disposition, request.reservation_id, request.account_id,
                                 policy_sha, request_sha, MappingProxyType(dict(sorted(active.items()))),
                                 MappingProxyType(dict(policy.limits)), str(row["event_sha256"]))


class _Connection:
    """Commit on success and roll back any failed multi-resource reservation."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def __enter__(self) -> sqlite3.Connection:
        return self.connection

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if exc_type is not None:
                self.connection.rollback()
            elif self.connection.in_transaction:
                self.connection.commit()
        finally:
            self.connection.close()


__all__ = ["DurableReservationLedger", "ReservationDisposition", "ReservationError",
           "ReservationPolicy", "ReservationRequest", "ReservationResult"]
