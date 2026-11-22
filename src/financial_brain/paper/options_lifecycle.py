"""Append-only, paper-only option leg fills and recovery accounting.

This journal deliberately separates economic risk reserve from broker margin and
expiry scenarios from exchange settlement. It never sends orders to a broker.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from functools import wraps
from typing import Any

from financial_brain.forward_edge.options_risk import (
    OptionLegQuote,
    OptionsRiskError,
    validate_supported_paper_option_structure,
)


class OptionLifecycleError(ValueError):
    """Invalid option paper lifecycle event or unsafe leg transition."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise OptionLifecycleError(f"{label} must be timezone aware")


def _canonical(value: dict[str, Any]) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise OptionLifecycleError(f"event is not canonical JSON: {exc}") from exc


def _atomic(method):
    """Serialize state validation and journal append in one SQLite transaction."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        owns_transaction = not self.connection.in_transaction
        savepoint = "fb_option_lifecycle_atomic"
        if owns_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        else:
            self.connection.execute(f"SAVEPOINT {savepoint}")
        try:
            result = method(self, *args, **kwargs)
            if owns_transaction:
                self.connection.commit()
            else:
                self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            return result
        except Exception:
            if owns_transaction:
                self.connection.rollback()
            else:
                self.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
    return wrapped


@dataclass(frozen=True)
class OptionLegObservation:
    """Entry leg quote with point-in-time source and custody fields."""

    leg: OptionLegQuote
    available_at: datetime
    source: str
    exchange: str
    evidence_sha256: str

    def validate(self, *, as_of: datetime, max_age_seconds: float) -> None:
        _aware(as_of, "as_of")
        _aware(self.leg.observed_at, "quote observed_at")
        _aware(self.available_at, "quote available_at")
        if (not self.source.strip() or not self.exchange.strip()
                or not _SHA256.fullmatch(self.evidence_sha256)):
            raise OptionLifecycleError("quote source, exchange and lowercase evidence SHA-256 are required")
        if self.leg.observed_at > self.available_at or self.available_at > as_of:
            raise OptionLifecycleError("quote is unavailable or has inconsistent source/availability clocks")
        if (as_of - self.available_at).total_seconds() > max_age_seconds:
            raise OptionLifecycleError("quote is stale at the option paper decision")
        try:
            self.leg.validate(as_of=as_of, quantity=self.leg.lot_size)
        except OptionsRiskError as exc:
            raise OptionLifecycleError(str(exc)) from exc


class PaperOptionLifecycle:
    """SQLite event journal for safe partial option fills and crash recovery."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.execute("""CREATE TABLE IF NOT EXISTS option_lifecycle_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            previous_sha256 TEXT NOT NULL,
            event_sha256 TEXT NOT NULL,
            event_json TEXT NOT NULL,
            UNIQUE(plan_id, event_id))""")
        self.connection.executescript("""
            CREATE TRIGGER IF NOT EXISTS option_lifecycle_no_update
            BEFORE UPDATE ON option_lifecycle_events
            BEGIN SELECT RAISE(ABORT, 'option lifecycle is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS option_lifecycle_no_delete
            BEFORE DELETE ON option_lifecycle_events
            BEGIN SELECT RAISE(ABORT, 'option lifecycle is append-only'); END;
        """)
        self.connection.execute("""CREATE TABLE IF NOT EXISTS option_desk_control_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            desk_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            previous_sha256 TEXT NOT NULL,
            event_sha256 TEXT NOT NULL,
            event_json TEXT NOT NULL,
            UNIQUE(account_id,desk_id,event_id))""")
        self.connection.executescript("""
            CREATE TRIGGER IF NOT EXISTS option_desk_controls_no_update
            BEFORE UPDATE ON option_desk_control_events
            BEGIN SELECT RAISE(ABORT, 'option desk controls are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS option_desk_controls_no_delete
            BEFORE DELETE ON option_desk_control_events
            BEGIN SELECT RAISE(ABORT, 'option desk controls are append-only'); END;
        """)
        self.connection.execute("""CREATE TABLE IF NOT EXISTS option_paper_outbox (
            account_id TEXT NOT NULL,
            signal_id TEXT NOT NULL,
            child_order_id TEXT NOT NULL,
            plan_id TEXT NOT NULL UNIQUE,
            payload_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(account_id, signal_id, child_order_id))""")
        self.connection.executescript("""
            CREATE TRIGGER IF NOT EXISTS option_paper_outbox_no_update
            BEFORE UPDATE ON option_paper_outbox
            BEGIN SELECT RAISE(ABORT, 'paper option outbox is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS option_paper_outbox_no_delete
            BEFORE DELETE ON option_paper_outbox
            BEGIN SELECT RAISE(ABORT, 'paper option outbox is append-only'); END;
        """)

    def _events(self, plan_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT event_id,event_type,occurred_at,previous_sha256,event_sha256,event_json "
            "FROM option_lifecycle_events WHERE plan_id=? ORDER BY sequence", [plan_id]).fetchall()
        previous = "GENESIS"
        previous_time: str | None = None
        events = []
        for event_id, kind, occurred_at, stored_previous, digest, encoded in rows:
            payload = json.loads(encoded)
            expected = hashlib.sha256((previous + _canonical(payload)).encode("utf-8")).hexdigest()
            if stored_previous != previous or digest != expected or (previous_time and occurred_at < previous_time):
                raise OptionLifecycleError("option lifecycle journal hash chain is corrupt")
            events.append({"event_id": event_id, "type": kind, "occurred_at": occurred_at,
                           "payload": payload, "sha256": digest})
            previous = digest
            previous_time = occurred_at
        return events

    def _append(self, *, plan_id: str, event_id: str, event_type: str,
                occurred_at: datetime, payload: dict[str, Any],
                outbox_identity: dict[str, str] | None = None) -> bool:
        if not plan_id.strip() or not event_id.strip() or not event_type.strip():
            raise OptionLifecycleError("plan, event and event type identities are required")
        _aware(occurred_at, "occurred_at")
        encoded = _canonical(payload)
        stamp = occurred_at.astimezone(timezone.utc).isoformat()
        owns_transaction = not self.connection.in_transaction
        if owns_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        try:
            prior = self.connection.execute(
                "SELECT event_type,event_json FROM option_lifecycle_events WHERE plan_id=? AND event_id=?",
                [plan_id, event_id]).fetchone()
            if prior:
                if prior != (event_type, encoded):
                    raise OptionLifecycleError("event identity conflicts with previously journaled evidence")
                if outbox_identity:
                    outbox_row = self.connection.execute(
                        "SELECT account_id,signal_id,child_order_id,plan_id,payload_sha256,created_at "
                        "FROM option_paper_outbox WHERE plan_id=?", [plan_id]).fetchone()
                    expected_outbox = (
                        outbox_identity["account_id"], outbox_identity["signal_id"],
                        outbox_identity["child_order_id"], plan_id,
                        hashlib.sha256(encoded.encode("utf-8")).hexdigest(), stamp)
                    if outbox_row != expected_outbox:
                        raise OptionLifecycleError("plan event is missing or conflicts with its atomic outbox record")
                if owns_transaction:
                    self.connection.commit()
                return False
            previous_row = self.connection.execute(
                "SELECT event_sha256,occurred_at FROM option_lifecycle_events "
                "WHERE plan_id=? ORDER BY sequence DESC LIMIT 1",
                [plan_id]).fetchone()
            previous = previous_row[0] if previous_row else "GENESIS"
            if previous_row and stamp < previous_row[1]:
                raise OptionLifecycleError("option lifecycle event time cannot regress")
            if outbox_identity:
                outbox_row = self.connection.execute(
                    "SELECT account_id,signal_id,child_order_id,plan_id FROM option_paper_outbox "
                    "WHERE plan_id=? OR (account_id=? AND signal_id=? AND child_order_id=?)",
                    [plan_id, outbox_identity["account_id"], outbox_identity["signal_id"],
                     outbox_identity["child_order_id"]]).fetchone()
                if outbox_row:
                    raise OptionLifecycleError("signal/child order already has a durable paper execution intent")
            digest = hashlib.sha256((previous + encoded).encode("utf-8")).hexdigest()
            self.connection.execute(
                "INSERT INTO option_lifecycle_events "
                "(plan_id,event_id,event_type,occurred_at,previous_sha256,event_sha256,event_json) "
                "VALUES(?,?,?,?,?,?,?)",
                [plan_id, event_id, event_type, stamp, previous, digest, encoded])
            if outbox_identity:
                self.connection.execute(
                    "INSERT INTO option_paper_outbox "
                    "(account_id,signal_id,child_order_id,plan_id,payload_sha256,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    [outbox_identity["account_id"], outbox_identity["signal_id"],
                     outbox_identity["child_order_id"], plan_id,
                     hashlib.sha256(encoded.encode("utf-8")).hexdigest(), stamp])
            if owns_transaction:
                self.connection.commit()
            return True
        except Exception:
            if owns_transaction:
                self.connection.rollback()
            raise

    def _existing_result(self, *, plan_id: str, event_id: str,
                         event_type: str, payload: dict[str, Any]) -> bool | None:
        prior = self.connection.execute(
            "SELECT event_type,event_json FROM option_lifecycle_events WHERE plan_id=? AND event_id=?",
            [plan_id, event_id]).fetchone()
        if prior is None:
            return None
        if prior != (event_type, _canonical(payload)):
            raise OptionLifecycleError("event identity conflicts with previously journaled evidence")
        return False

    @_atomic
    def register_plan(self, *, plan_id: str, account_id: str, desk_id: str,
                      signal_id: str, signal_expires_at: datetime,
                      child_order_id: str, decision_at: datetime,
                      quantity: int, observations: tuple[OptionLegObservation, ...],
                      fee_reserve_inr: Decimal, max_quote_age_seconds: float,
                      cost_model_sha256: str) -> bool:
        """Freeze a long-option/debit-vertical paper plan and its cash reserve."""
        _aware(decision_at, "decision_at")
        _aware(signal_expires_at, "signal_expires_at")
        if signal_expires_at <= decision_at:
            raise OptionLifecycleError("signal expiration must follow its decision time")
        if (not plan_id.strip() or not account_id.strip() or not desk_id.strip() or not signal_id.strip()
                or not child_order_id.strip() or type(quantity) is not int
                or quantity < 1 or not isinstance(fee_reserve_inr, Decimal)):
            raise OptionLifecycleError("paper account, signal/child identity, plan and positive quantity are required")
        if (not fee_reserve_inr.is_finite() or fee_reserve_inr <= 0
                or not math.isfinite(max_quote_age_seconds) or max_quote_age_seconds < 0):
            raise OptionLifecycleError("fee reserve must be positive and quote-age policy finite and non-negative")
        if not _SHA256.fullmatch(cost_model_sha256):
            raise OptionLifecycleError("cost model needs a lowercase SHA-256 identity")
        if not observations:
            raise OptionLifecycleError("at least one observed option leg is required")
        for item in observations:
            item.validate(as_of=decision_at, max_age_seconds=max_quote_age_seconds)
        legs = tuple(item.leg for item in observations)
        lot_sizes = {leg.lot_size for leg in legs}
        if len(lot_sizes) != 1 or quantity % next(iter(lot_sizes)):
            raise OptionLifecycleError("planned quantity must be whole lots shared by all legs")
        try:
            structure = validate_supported_paper_option_structure(legs=legs, quantity=quantity)
        except OptionsRiskError as exc:
            raise OptionLifecycleError(str(exc)) from exc
        premium_risk = Decimal(str(structure["economic_max_loss_inr"]))
        reserve = premium_risk + fee_reserve_inr
        frozen_legs = []
        for item in observations:
            leg = item.leg
            frozen_legs.append({
                "leg_id": leg.leg_id, "side": leg.side, "token": leg.token,
                "symbol": leg.symbol, "underlying": leg.underlying,
                "option_type": leg.option_type, "strike": leg.strike,
                "expiry": leg.expiry.isoformat(), "lot_size": leg.lot_size,
                "bid": leg.bid, "ask": leg.ask, "bid_qty": leg.bid_qty, "ask_qty": leg.ask_qty,
                "quote_observed_at": leg.observed_at.isoformat(),
                "quote_available_at": item.available_at.isoformat(),
                "source": item.source, "exchange": item.exchange,
                "evidence_sha256": item.evidence_sha256,
            })
        payload = {
            "account_id": account_id, "desk_id": desk_id, "signal_id": signal_id,
            "signal_expires_at": signal_expires_at.isoformat(),
            "child_order_id": child_order_id, "decision_at": decision_at.isoformat(),
            "target_quantity": quantity, "legs": frozen_legs,
            "strategy_type": structure["strategy_type"],
            "premium_risk_inr": str(premium_risk), "fee_reserve_inr": str(fee_reserve_inr),
            "cash_reserve_inr": str(reserve), "margin_status": "NOT_VERIFIED",
            "cost_model_sha256": cost_model_sha256,
            "max_quote_age_seconds": max_quote_age_seconds,
            "simulation_fidelity": "OBSERVED_QUOTE_SIM",
        }
        return self._append(plan_id=plan_id, event_id="PLAN", event_type="PLAN_REGISTERED",
                            occurred_at=decision_at, payload=payload,
                            outbox_identity={"account_id": account_id, "signal_id": signal_id,
                                             "child_order_id": child_order_id})

    def pending_outbox(self, *, account_id: str | None = None) -> list[dict[str, str]]:
        """Return immutable plan intents awaiting a paper consumer; never a broker queue."""
        if account_id is None:
            rows = self.connection.execute(
                "SELECT account_id,signal_id,child_order_id,plan_id,payload_sha256,created_at "
                "FROM option_paper_outbox ORDER BY created_at,plan_id").fetchall()
        else:
            rows = self.connection.execute(
                "SELECT account_id,signal_id,child_order_id,plan_id,payload_sha256,created_at "
                "FROM option_paper_outbox WHERE account_id=? ORDER BY created_at,plan_id", [account_id]).fetchall()
        return [{"account_id": row[0], "signal_id": row[1], "child_order_id": row[2],
                 "plan_id": row[3], "payload_sha256": row[4], "created_at": row[5],
                 "dispatch_status": "PENDING_PAPER_CONSUMER"} for row in rows]

    @_atomic
    def invalidate_signal(self, *, plan_id: str, event_id: str,
                          occurred_at: datetime, reason: str) -> bool:
        plan = self.state(plan_id)
        _aware(occurred_at, "invalidation time")
        if not event_id.strip() or not reason.strip():
            raise OptionLifecycleError("signal invalidation needs an event ID and reason")
        payload = {"reason": reason}
        journal_id = f"SIGNAL_INVALIDATED:{event_id}"
        duplicate = self._existing_result(plan_id=plan_id, event_id=journal_id,
                                          event_type="SIGNAL_INVALIDATED", payload=payload)
        if duplicate is not None:
            return duplicate
        if any(event["type"] == "SIGNAL_INVALIDATED" for event in plan["events"]):
            raise OptionLifecycleError("signal is already invalidated")
        return self._append(plan_id=plan_id, event_id=journal_id,
                            event_type="SIGNAL_INVALIDATED", occurred_at=occurred_at,
                            payload=payload)

    def _desk_control_state(self, account_id: str, desk_id: str) -> str:
        rows = self.connection.execute(
            "SELECT event_type,occurred_at,previous_sha256,event_sha256,event_json "
            "FROM option_desk_control_events WHERE account_id=? AND desk_id=? ORDER BY sequence",
            [account_id, desk_id]).fetchall()
        previous = "GENESIS"
        previous_time: str | None = None
        state = "ACTIVE"
        for event_type, occurred_at, stored_previous, digest, encoded in rows:
            expected = hashlib.sha256((previous + encoded).encode("utf-8")).hexdigest()
            if stored_previous != previous or digest != expected or (previous_time and occurred_at < previous_time):
                raise OptionLifecycleError("option desk control hash chain is corrupt")
            if event_type not in {"HALTED", "RESUMED"}:
                raise OptionLifecycleError("option desk control event has an unsupported state")
            state = "HALTED" if event_type == "HALTED" else "ACTIVE"
            previous, previous_time = digest, occurred_at
        return state

    def _append_desk_control(self, *, account_id: str, desk_id: str, event_id: str,
                             event_type: str, occurred_at: datetime,
                             payload: dict[str, Any]) -> bool:
        if not account_id.strip() or not desk_id.strip() or not event_id.strip():
            raise OptionLifecycleError("desk control needs account, desk and event identities")
        _aware(occurred_at, "desk control time")
        encoded = _canonical(payload)
        stamp = occurred_at.astimezone(timezone.utc).isoformat()
        prior = self.connection.execute(
            "SELECT event_type,event_json FROM option_desk_control_events "
            "WHERE account_id=? AND desk_id=? AND event_id=?",
            [account_id, desk_id, event_id]).fetchone()
        if prior:
            if prior != (event_type, encoded):
                raise OptionLifecycleError("desk control ID conflicts with prior evidence")
            return False
        previous_row = self.connection.execute(
            "SELECT event_sha256,occurred_at FROM option_desk_control_events "
            "WHERE account_id=? AND desk_id=? ORDER BY sequence DESC LIMIT 1",
            [account_id, desk_id]).fetchone()
        previous = previous_row[0] if previous_row else "GENESIS"
        if previous_row and stamp < previous_row[1]:
            raise OptionLifecycleError("desk control event time cannot regress")
        digest = hashlib.sha256((previous + encoded).encode("utf-8")).hexdigest()
        self.connection.execute(
            "INSERT INTO option_desk_control_events "
            "(account_id,desk_id,event_id,event_type,occurred_at,previous_sha256,event_sha256,event_json) "
            "VALUES(?,?,?,?,?,?,?,?)",
            [account_id, desk_id, event_id, event_type, stamp, previous, digest, encoded])
        return True

    @_atomic
    def halt_desk_on_stale_feed(self, *, account_id: str, desk_id: str,
                                event_id: str, occurred_at: datetime, reason: str,
                                source: str, exchange: str, quote_age_seconds: float,
                                max_quote_age_seconds: float, evidence_sha256: str) -> bool:
        if (not reason.strip() or not source.strip() or not exchange.strip()
                or not math.isfinite(quote_age_seconds) or not math.isfinite(max_quote_age_seconds)
                or quote_age_seconds <= max_quote_age_seconds or max_quote_age_seconds <= 0
                or not _SHA256.fullmatch(evidence_sha256)):
            raise OptionLifecycleError("stale-feed halt requires overdue quote and hashed source evidence")
        return self._append_desk_control(account_id=account_id, desk_id=desk_id,
            event_id=event_id, event_type="HALTED", occurred_at=occurred_at,
            payload={"reason": reason, "source": source, "exchange": exchange,
                     "quote_age_seconds": quote_age_seconds,
                     "max_quote_age_seconds": max_quote_age_seconds,
                     "evidence_sha256": evidence_sha256})

    @_atomic
    def resume_desk(self, *, account_id: str, desk_id: str, event_id: str,
                    occurred_at: datetime, authorized_by: str, source: str,
                    exchange: str, healthy_quote_count: int, quote_age_seconds: float,
                    max_quote_age_seconds: float, evidence_sha256: str) -> bool:
        payload = {"authorized_by": authorized_by, "source": source, "exchange": exchange,
                   "healthy_quote_count": healthy_quote_count,
                   "quote_age_seconds": quote_age_seconds,
                   "max_quote_age_seconds": max_quote_age_seconds,
                   "evidence_sha256": evidence_sha256}
        prior = self.connection.execute(
            "SELECT event_type,event_json FROM option_desk_control_events "
            "WHERE account_id=? AND desk_id=? AND event_id=?",
            [account_id, desk_id, event_id]).fetchone()
        if prior:
            if prior != ("RESUMED", _canonical(payload)):
                raise OptionLifecycleError("desk control ID conflicts with prior evidence")
            return False
        if self._desk_control_state(account_id, desk_id) != "HALTED":
            raise OptionLifecycleError("desk resume requires a recorded HALTED state")
        if (not authorized_by.strip() or not source.strip() or not exchange.strip()
                or type(healthy_quote_count) is not int or healthy_quote_count < 3
                or not math.isfinite(quote_age_seconds) or not math.isfinite(max_quote_age_seconds)
                or quote_age_seconds < 0 or max_quote_age_seconds <= 0
                or quote_age_seconds > max_quote_age_seconds
                or not _SHA256.fullmatch(evidence_sha256)):
            raise OptionLifecycleError("desk resume requires owner approval and three fresh evidenced quotes")
        return self._append_desk_control(account_id=account_id, desk_id=desk_id,
            event_id=event_id, event_type="RESUMED", occurred_at=occurred_at,
            payload=payload)

    @_atomic
    def record_entry_fill(self, *, plan_id: str, fill_id: str, leg_id: str,
                          quantity: int, price: Decimal, fees_inr: Decimal,
                          occurred_at: datetime, quote_observed_at: datetime,
                          quote_available_at: datetime, bid: Decimal, ask: Decimal,
                          bid_qty: int, ask_qty: int, source: str, exchange: str,
                          evidence_sha256: str, token: int, symbol: str,
                          expiry: str, lot_size: int) -> bool:
        plan = self.state(plan_id)
        leg = self._leg(plan, leg_id)
        self._validate_event_quote(occurred_at=occurred_at, quote_observed_at=quote_observed_at,
                                   quote_available_at=quote_available_at, price=price, bid=bid,
                                   ask=ask, bid_qty=bid_qty, ask_qty=ask_qty,
                                   source=source, exchange=exchange, evidence_sha256=evidence_sha256,
                                   max_age_seconds=plan["max_quote_age_seconds"], token=token,
                                   symbol=symbol, expiry=expiry, lot_size=lot_size, expected_leg=leg)
        if (not fill_id.strip() or type(quantity) is not int or type(token) is not int
                or not isinstance(price, Decimal) or not isinstance(fees_inr, Decimal)
                or quantity < 1 or quantity % leg["lot_size"]
                or price <= 0 or not fees_inr.is_finite() or fees_inr < 0):
            raise OptionLifecycleError("entry fill must be positive whole lots with valid price and fees")
        payload = {"leg_id": leg_id, "side": leg["side"], "quantity": quantity,
                   "price": str(price), "fees_inr": str(fees_inr), "bid": str(bid), "ask": str(ask),
                   "quote_observed_at": quote_observed_at.isoformat(),
                   "quote_available_at": quote_available_at.isoformat(), "source": source,
                   "exchange": exchange, "evidence_sha256": evidence_sha256}
        event_id = f"ENTRY_FILL:{fill_id}"
        duplicate = self._existing_result(plan_id=plan_id, event_id=event_id,
                                          event_type="ENTRY_FILL", payload=payload)
        if duplicate is not None:
            return duplicate
        if occurred_at >= datetime.fromisoformat(plan["signal_expires_at"]):
            raise OptionLifecycleError("expired signal cannot create a delayed option fill")
        if any(event["type"] == "SIGNAL_INVALIDATED" for event in plan["events"]):
            raise OptionLifecycleError("invalidated signal cannot create an option fill")
        if self._desk_control_state(plan["account_id"], plan["desk_id"]) == "HALTED":
            raise OptionLifecycleError("option desk is HALTED; new entry fills are blocked")
        if Decimal(plan["entry_fees_inr"]) + fees_inr > Decimal(plan["fee_reserve_inr"]):
            raise OptionLifecycleError("entry fill would exceed the frozen fee reserve")
        if quantity > (ask_qty if leg["side"] == "BUY" else bid_qty):
            raise OptionLifecycleError("entry fill exceeds displayed executable quote depth")
        if any(event["type"] == "ENTRY_CANCELLED" and event["payload"]["leg_id"] == leg_id
               for event in plan["events"]):
            raise OptionLifecycleError("entry fill arrived after the leg remainder was cancelled")
        if (leg["side"] == "BUY" and price < ask) or (leg["side"] == "SELL" and price > bid):
            raise OptionLifecycleError("entry fill is better than its executable ask/bid reference")
        open_qty = plan["open_quantity_by_leg"]
        if open_qty[leg_id] + quantity > plan["target_quantity"]:
            raise OptionLifecycleError("entry fill exceeds the frozen plan quantity")
        protective = next((row["leg_id"] for row in plan["legs"] if row["side"] == "BUY"), None)
        if leg["side"] == "SELL" and (protective is None or
                                        open_qty[protective] < open_qty[leg_id] + quantity):
            raise OptionLifecycleError("short fill exceeds already-filled protective long quantity")
        return self._append(plan_id=plan_id, event_id=event_id,
                            event_type="ENTRY_FILL", occurred_at=occurred_at, payload=payload)

    @_atomic
    def record_close_fill(self, *, plan_id: str, fill_id: str, leg_id: str,
                          quantity: int, price: Decimal, fees_inr: Decimal,
                          occurred_at: datetime, quote_observed_at: datetime,
                          quote_available_at: datetime, bid: Decimal, ask: Decimal,
                          bid_qty: int, ask_qty: int, source: str, exchange: str,
                          evidence_sha256: str, token: int, symbol: str,
                          expiry: str, lot_size: int) -> bool:
        plan = self.state(plan_id)
        leg = self._leg(plan, leg_id)
        self._validate_event_quote(occurred_at=occurred_at, quote_observed_at=quote_observed_at,
                                   quote_available_at=quote_available_at, price=price, bid=bid,
                                   ask=ask, bid_qty=bid_qty, ask_qty=ask_qty,
                                   source=source, exchange=exchange, evidence_sha256=evidence_sha256,
                                   max_age_seconds=plan["max_quote_age_seconds"], token=token,
                                   symbol=symbol, expiry=expiry, lot_size=lot_size, expected_leg=leg)
        close_side = "SELL" if leg["side"] == "BUY" else "BUY"
        payload = {"leg_id": leg_id, "entry_side": leg["side"], "close_side": close_side,
                   "quantity": quantity, "price": str(price), "fees_inr": str(fees_inr),
                   "bid": str(bid), "ask": str(ask),
                   "quote_observed_at": quote_observed_at.isoformat(),
                   "quote_available_at": quote_available_at.isoformat(), "source": source,
                   "exchange": exchange, "evidence_sha256": evidence_sha256}
        event_id = f"CLOSE_FILL:{fill_id}"
        duplicate = self._existing_result(plan_id=plan_id, event_id=event_id,
                                          event_type="CLOSE_FILL", payload=payload)
        if duplicate is not None:
            return duplicate
        open_qty = plan["open_quantity_by_leg"]
        if (not fill_id.strip() or type(quantity) is not int or type(token) is not int
                or not isinstance(price, Decimal) or not isinstance(fees_inr, Decimal)
                or quantity < 1 or quantity % leg["lot_size"] or quantity > open_qty[leg_id]):
            raise OptionLifecycleError("close fill must be positive whole lots within the open leg quantity")
        depth = bid_qty if close_side == "SELL" else ask_qty
        if quantity > depth:
            raise OptionLifecycleError("close fill exceeds displayed executable quote depth")
        if (close_side == "SELL" and price > bid) or (close_side == "BUY" and price < ask):
            raise OptionLifecycleError("close fill is better than its executable bid/ask reference")
        if not fees_inr.is_finite() or fees_inr < 0 or price <= 0:
            raise OptionLifecycleError("close fill needs a positive price and non-negative fees")
        protective = next((row["leg_id"] for row in plan["legs"] if row["side"] == "BUY"), None)
        short_leg = next((row["leg_id"] for row in plan["legs"] if row["side"] == "SELL"), None)
        if leg["side"] == "BUY" and short_leg and open_qty[protective] - quantity < open_qty[short_leg]:
            raise OptionLifecycleError("cannot close protection while an uncovered short leg remains")
        return self._append(plan_id=plan_id, event_id=event_id,
                            event_type="CLOSE_FILL", occurred_at=occurred_at, payload=payload)

    @_atomic
    def cancel_entry(self, *, plan_id: str, event_id: str, leg_id: str,
                     occurred_at: datetime, reason: str) -> bool:
        plan = self.state(plan_id)
        self._leg(plan, leg_id)
        payload = {"leg_id": leg_id, "reason": reason}
        journal_id = f"CANCEL:{event_id}"
        duplicate = self._existing_result(plan_id=plan_id, event_id=journal_id,
                                          event_type="ENTRY_CANCELLED", payload=payload)
        if duplicate is not None:
            return duplicate
        if any(event["type"] == "ENTRY_CANCELLED" and event["payload"]["leg_id"] == leg_id
               for event in plan["events"]):
            raise OptionLifecycleError("entry leg is already cancelled")
        if not event_id.strip() or not reason.strip():
            raise OptionLifecycleError("cancellation reason is required")
        return self._append(plan_id=plan_id, event_id=journal_id,
                            event_type="ENTRY_CANCELLED", occurred_at=occurred_at,
                            payload=payload)

    @_atomic
    def record_expiry_scenario(self, *, plan_id: str, event_id: str,
                               occurred_at: datetime, underlying_price: Decimal,
                               settlement_mode: str) -> bool:
        plan = self.state(plan_id)
        _aware(occurred_at, "expiry scenario time")
        if settlement_mode not in {"CASH_SETTLED", "PHYSICAL_DELIVERY"}:
            raise OptionLifecycleError("expiry scenario requires an explicit settlement mode")
        if (not event_id.strip() or not isinstance(underlying_price, Decimal)
                or not underlying_price.is_finite() or underlying_price <= 0):
            raise OptionLifecycleError("expiry scenario underlying price must be positive and finite")
        latest_expiry = max(leg["expiry"] for leg in plan["legs"])
        if occurred_at.date().isoformat() < latest_expiry:
            raise OptionLifecycleError("expiry scenario cannot be recorded before the contract expiry date")
        payload = {"underlying_price": str(underlying_price),
                   "settlement_mode": settlement_mode, "status": "SCENARIO_ONLY_NOT_SETTLEMENT",
                   "open_quantity_by_leg": plan["open_quantity_by_leg"]}
        return self._append(plan_id=plan_id, event_id=f"EXPIRY_SCENARIO:{event_id}",
                            event_type="EXPIRY_SCENARIO", occurred_at=occurred_at, payload=payload)

    def state(self, plan_id: str) -> dict[str, Any]:
        events = self._events(plan_id)
        if not events or events[0]["type"] != "PLAN_REGISTERED":
            raise OptionLifecycleError("paper option plan is missing its frozen registration event")
        plan = dict(events[0]["payload"])
        legs = plan["legs"]
        open_by_leg = {leg["leg_id"]: 0 for leg in legs}
        entry_fees = Decimal(0)
        close_fees = Decimal(0)
        cash = Decimal(0)
        fills = set()
        cancelled = set()
        for event in events[1:]:
            payload = event["payload"]
            if event["type"] in {"EXPIRY_SCENARIO", "SIGNAL_INVALIDATED"}:
                continue
            leg = self._leg_from_rows(legs, payload["leg_id"])
            quantity = payload.get("quantity", 0)
            if event["type"] == "ENTRY_FILL":
                if payload["side"] != leg["side"] or quantity < 1 or quantity % leg["lot_size"]:
                    raise OptionLifecycleError("entry fill contradicts the frozen leg or lot size")
                fill_id = event["event_id"]
                if fill_id in fills:
                    raise OptionLifecycleError("duplicate fill event in lifecycle history")
                fills.add(fill_id)
                open_by_leg[leg["leg_id"]] += quantity
                sign = Decimal(-1) if leg["side"] == "BUY" else Decimal(1)
                fees = Decimal(payload["fees_inr"])
                cash += sign * Decimal(payload["price"]) * quantity - fees
                entry_fees += fees
            elif event["type"] == "CLOSE_FILL":
                expected_close = "SELL" if leg["side"] == "BUY" else "BUY"
                if (payload["entry_side"] != leg["side"] or payload["close_side"] != expected_close
                        or quantity < 1 or quantity % leg["lot_size"]):
                    raise OptionLifecycleError("close fill contradicts the frozen leg or lot size")
                if quantity > open_by_leg[leg["leg_id"]]:
                    raise OptionLifecycleError("close event exceeds preceding open position")
                open_by_leg[leg["leg_id"]] -= quantity
                sign = Decimal(1) if payload["close_side"] == "SELL" else Decimal(-1)
                fees = Decimal(payload["fees_inr"])
                cash += sign * Decimal(payload["price"]) * quantity - fees
                close_fees += fees
            elif event["type"] == "ENTRY_CANCELLED":
                cancelled.add(leg["leg_id"])
        short = next((leg["leg_id"] for leg in legs if leg["side"] == "SELL"), None)
        protective = next((leg["leg_id"] for leg in legs if leg["side"] == "BUY"), None)
        if short and open_by_leg[short] > open_by_leg[protective]:
            raise OptionLifecycleError("journal reconstructs an uncovered short option position")
        expiry_unresolved = any(event["type"] == "EXPIRY_SCENARIO" for event in events) and any(open_by_leg.values())
        total_filled = sum(open_by_leg.values())
        signal_invalidated = any(event["type"] == "SIGNAL_INVALIDATED" for event in events)
        fees_total = entry_fees + close_fees
        fee_reserve = Decimal(plan["fee_reserve_inr"])
        if expiry_unresolved:
            status = "OPEN_AT_EXPIRY_UNRESOLVED"
        elif signal_invalidated:
            status = ("OPEN_SIGNAL_INVALIDATED" if total_filled else
                      "CLOSED" if any(event["type"] == "CLOSE_FILL" for event in events) else
                      "INVALIDATED_UNFILLED")
        elif not total_filled:
            status = ("CLOSED" if any(event["type"] == "CLOSE_FILL" for event in events)
                      else "CANCELLED_UNFILLED" if cancelled else "UNFILLED")
        elif any(leg_id in cancelled for leg_id in open_by_leg):
            status = "PARTIALLY_OPEN_CANCELLED_REMAINDER"
        elif all(open_by_leg[leg["leg_id"]] == plan["target_quantity"] for leg in legs):
            status = "OPEN"
        else:
            status = "PARTIALLY_OPEN"
        plan.update({"status": status, "open_quantity_by_leg": open_by_leg,
                     "signal_invalidated": signal_invalidated,
                     "desk_state": self._desk_control_state(plan["account_id"], plan["desk_id"]),
                     "entry_fees_inr": str(entry_fees), "close_fees_inr": str(close_fees),
                     "fees_total_inr": str(fees_total),
                     "fee_reserve_remaining_inr": str(max(Decimal(0), fee_reserve - fees_total)),
                     "fee_reserve_breached": fees_total > fee_reserve,
                     "net_cashflow_inr": str(cash), "event_count": len(events),
                     "events": events,
                     "margin_status": "NOT_VERIFIED",
                     "simulation_fidelity": "OBSERVED_QUOTE_SIM"})
        return plan

    @staticmethod
    def _leg(plan: dict[str, Any], leg_id: str) -> dict[str, Any]:
        return PaperOptionLifecycle._leg_from_rows(plan["legs"], leg_id)

    @staticmethod
    def _leg_from_rows(legs: list[dict[str, Any]], leg_id: str) -> dict[str, Any]:
        found = next((leg for leg in legs if leg["leg_id"] == leg_id), None)
        if found is None:
            raise OptionLifecycleError("event references an unknown option leg")
        return found

    @staticmethod
    def _validate_event_quote(*, occurred_at: datetime, quote_observed_at: datetime,
                              quote_available_at: datetime, price: Decimal, bid: Decimal,
                              ask: Decimal, bid_qty: int, ask_qty: int, source: str,
                              exchange: str, evidence_sha256: str,
                              max_age_seconds: float, token: int, symbol: str,
                              expiry: str, lot_size: int, expected_leg: dict[str, Any]) -> None:
        _aware(occurred_at, "fill time")
        _aware(quote_observed_at, "quote source time")
        _aware(quote_available_at, "quote availability time")
        if (quote_observed_at > quote_available_at or quote_available_at > occurred_at
                or not source.strip() or not exchange.strip()
                or not _SHA256.fullmatch(evidence_sha256)):
            raise OptionLifecycleError("fill needs chronological quote clocks and hashed source/exchange evidence")
        if occurred_at.date().isoformat() >= expiry:
            raise OptionLifecycleError("option fill is invalid on or after its expiry date")
        if (occurred_at - quote_available_at).total_seconds() > max_age_seconds:
            raise OptionLifecycleError("fill quote is stale under the frozen plan policy")
        if (token, symbol, expiry, lot_size) != (
                expected_leg["token"], expected_leg["symbol"],
                expected_leg["expiry"], expected_leg["lot_size"]):
            raise OptionLifecycleError("fill quote contract identity differs from the frozen option leg")
        if (type(token) is not int or token <= 0 or type(lot_size) is not int or lot_size < 1
                or not symbol.strip() or type(bid_qty) is not int or type(ask_qty) is not int
                or not isinstance(price, Decimal) or not isinstance(bid, Decimal) or not isinstance(ask, Decimal)
                or not price.is_finite() or not bid.is_finite() or not ask.is_finite()
                or price <= 0 or bid <= 0 or ask < bid or bid_qty < 0 or ask_qty < 0):
            raise OptionLifecycleError("fill quote and displayed depth must be positive and valid")


__all__ = ["OptionLegObservation", "OptionLifecycleError", "PaperOptionLifecycle"]
