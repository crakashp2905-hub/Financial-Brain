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

from financial_brain.portfolio.canonical_ledger import (
    CanonicalLedgerError,
    CanonicalPortfolioLedger,
    EventKind,
    PortfolioEvent,
)


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

    def record_paper_fill(self, *, reservation_id: str, fill: PortfolioEvent,
                          fee: PortfolioEvent | None = None) -> str:
        """Atomically append a canonical paper fill, its optional fee, and reservation binding.

        The reservation remains active after a fill: this records the position transfer
        evidence but does not yet replace order exposure with portfolio-level risk usage.
        Both append-only journals share this SQLite transaction, so restart replay cannot
        leave the fill unjournaled in one ledger while present in the other.
        """
        row = fill.normalized()
        if row["event_kind"] != EventKind.FILL.value:
            raise ReservationError("paper position transfer requires a fill event")
        fee_row = fee.normalized() if fee is not None else None
        if fee_row is not None:
            metadata = fee_row.get("metadata")
            if (fee_row["event_kind"] != EventKind.FEE.value
                    or fee_row["account_id"] != row["account_id"]
                    or fee_row["sleeve_id"] != row["sleeve_id"]
                    or fee_row["source_system"] != row["source_system"]
                    or fee_row["source_sha256"] != row["source_sha256"]
                    or fee_row["currency"] != row["currency"]
                    or fee_row["occurred_at"] != row["occurred_at"]
                    or not isinstance(metadata, dict)
                    or metadata.get("fill_event_id") != row["source_event_id"]):
                raise ReservationError("paper fee must be source-bound to its matching fill")
        account_id = str(row["account_id"])
        if not reservation_id.strip():
            raise ReservationError("reservation ID is required for paper fill transfer")
        try:
            CanonicalPortfolioLedger(self.path)
        except (OSError, sqlite3.Error) as exc:
            raise ReservationError(f"cannot initialize canonical portfolio journal: {exc}") from exc
        body = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)
        payload_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        position_event_id = f"POSITION:{reservation_id}:{row['source_event_id']}"
        position_receipt = {
            "account_id": account_id, "event_id": position_event_id,
            "reservation_id": reservation_id, "event_kind": "POSITION_FILL_RECORDED",
            "position_event_id": row["source_event_id"], "position_payload_sha256": payload_sha,
            "occurred_at": row["occurred_at"], "source_sha256": row["source_sha256"],
        }
        fee_body = json.dumps(fee_row, sort_keys=True, separators=(",", ":"), allow_nan=False) \
            if fee_row is not None else None
        fee_payload_sha = hashlib.sha256(fee_body.encode("utf-8")).hexdigest() if fee_body else None
        position_receipt["fee_payload_sha256"] = fee_payload_sha
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            reservation = connection.execute(
                "SELECT state,request_json FROM reservations WHERE account_id=? AND reservation_id=?",
                (account_id, reservation_id),
            ).fetchone()
            if reservation is None:
                raise ReservationError("paper fill has no matching account reservation")
            if reservation["state"] != "ACTIVE":
                raise ReservationError("paper fill cannot attach to a released reservation")
            reserved = json.loads(reservation["request_json"])["amounts"]
            quantity_resource = f"INSTRUMENT_QUANTITY:{row['instrument_id']}"
            fill_quantity = Decimal(str(row["quantity"]))
            position_action = row["position_action"] or row["side"]
            fee_amount = (-Decimal(str(fee_row["cash_delta"]))
                          if fee_row is not None else Decimal(0))
            if fee_amount > Decimal(reserved.get("PAPER_COST_BUFFER", "0")):
                raise ReservationError("paper fees exceed their frozen cost-buffer reservation")
            if fee_amount > Decimal(reserved.get("CASH", "0")):
                raise ReservationError("paper fees exceed their frozen cash reservation")
            if Decimal(reserved.get(quantity_resource, "0")) < fill_quantity:
                raise ReservationError("paper fill quantity exceeds its frozen instrument reservation")
            if position_action == "SELL":
                inventory_resource = f"SELLABLE_INVENTORY:{row['instrument_id']}"
                if Decimal(reserved.get(inventory_resource, "0")) < fill_quantity:
                    raise ReservationError("paper sell fill exceeds its frozen sellable-inventory reservation")
            elif position_action == "BUY" and row["asset_class"] != "FUTURE":
                required_cash = (fill_quantity * Decimal(str(row["price"]))
                                 * Decimal(str(row["multiplier"])) + fee_amount)
                if Decimal(reserved.get("CASH", "0")) < required_cash:
                    raise ReservationError("paper buy fill exceeds its frozen cash reservation")
                if (row["asset_class"] == "OPTION"
                        and Decimal(reserved.get("OPTION_BUYING_POWER", "0"))
                        < fill_quantity * Decimal(str(row["price"])) * Decimal(str(row["multiplier"]))
                        + fee_amount):
                    raise ReservationError("option fill exceeds its frozen options-buying-power reservation")
            elif row["asset_class"] == "FUTURE" and position_action == "BUY":
                if any(Decimal(reserved.get(resource, "0")) <= 0
                       for resource in ("MARGIN", "RISK_CAPITAL")):
                    raise ReservationError("futures fill requires frozen margin and risk-capital reservations")
            prior_transfer = connection.execute(
                "SELECT event_sha256 FROM reservation_events WHERE account_id=? AND event_id=?",
                (account_id, position_event_id),
            ).fetchone()
            transfer_sha = _digest(position_receipt)
            if prior_transfer is not None and prior_transfer["event_sha256"] != transfer_sha:
                raise ReservationError("paper position fill replay conflicts with prior transfer evidence")

            existing = connection.execute(
                "SELECT payload_sha256 FROM portfolio_events WHERE account_id=? AND source_system=? "
                "AND source_event_id=?",
                (row["account_id"], row["source_system"], row["source_event_id"]),
            ).fetchone()
            if existing is not None and existing["payload_sha256"] != payload_sha:
                raise ReservationError("paper position event ID conflicts with existing canonical fill")
            if existing is None:
                connection.execute(
                    "INSERT INTO portfolio_events "
                    "(account_id,sleeve_id,source_system,source_event_id,source_sha256,payload_sha256,event_json) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (row["account_id"], row["sleeve_id"], row["source_system"], row["source_event_id"],
                     row["source_sha256"], payload_sha, body),
                )
                try:
                    CanonicalPortfolioLedger._validate_position_transitions(connection, account_id)
                except CanonicalLedgerError as exc:
                    raise ReservationError(f"canonical position transition denied: {exc}") from exc
            if fee_row is not None and fee_body is not None and fee_payload_sha is not None:
                existing_fee = connection.execute(
                    "SELECT payload_sha256 FROM portfolio_events WHERE account_id=? AND source_system=? "
                    "AND source_event_id=?",
                    (fee_row["account_id"], fee_row["source_system"], fee_row["source_event_id"]),
                ).fetchone()
                if existing_fee is not None and existing_fee["payload_sha256"] != fee_payload_sha:
                    raise ReservationError("paper fee event ID conflicts with existing canonical fee")
                if existing_fee is None:
                    connection.execute(
                        "INSERT INTO portfolio_events "
                        "(account_id,sleeve_id,source_system,source_event_id,source_sha256,payload_sha256,event_json) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (fee_row["account_id"], fee_row["sleeve_id"], fee_row["source_system"],
                         fee_row["source_event_id"], fee_row["source_sha256"], fee_payload_sha, fee_body),
                    )
            if prior_transfer is None:
                self._append_event(connection, position_receipt)
            return payload_sha

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
