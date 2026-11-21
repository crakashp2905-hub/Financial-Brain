"""Restart-safe paper exit protection reducer backed by the hash-chained journal."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from financial_brain.forward_edge.durable import DurableJournal

from .position_signals import (
    EmittedExitEvent,
    EntrySignal,
    EntrySide,
    ExitProtectionIntent,
    PositionSignalError,
    validate_protection_emission,
)
from .trader_signal import ExitTrigger


class ExitLedgerError(ValueError):
    """Invalid, duplicate, or unsafe position-protection transition."""


def _decimal(value: object, name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ExitLedgerError(f"{name} must be decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ExitLedgerError(f"{name} must be finite and nonnegative")
    return parsed


@dataclass(frozen=True, slots=True)
class ExitLedgerState:
    entry_filled_quantity: Decimal
    exit_filled_quantity: Decimal
    exit_pending_quantity: Decimal
    open_quantity: Decimal
    armed_protection_ids: frozenset[str]
    emitted_idempotency_keys: frozenset[str]
    unresolved_exit_intent_ids: frozenset[str]


class ExitProtectionLedger:
    """Append-only, replayable position exit state; caller owns journal lifetime."""

    def __init__(self, journal: DurableJournal, *, position_id: str) -> None:
        self.journal = journal
        self.position_id = position_id
        if not position_id.strip():
            raise ExitLedgerError("position_id is required")

    def _position_records(self) -> list[dict]:
        return [r for r in self.journal.records() if r.get("position_id") == self.position_id]

    @staticmethod
    def _append_idempotent(journal: DurableJournal, records: list[dict], record: dict, key: str) -> bool:
        previous = next((r for r in records if r.get("idempotency_key") == key), None)
        if previous is not None:
            if previous != record:
                raise ExitLedgerError("idempotency key conflicts with previously journaled evidence")
            return False
        journal.append(record)
        return True

    @property
    def state(self) -> ExitLedgerState:
        records = self._position_records()
        entries = {r["fill_id"]: _decimal(r["quantity"], "entry fill")
                   for r in records if r["type"] == "ENTRY_FILL"}
        protections = {r["protection_id"] for r in records if r["type"] == "PROTECTION_ARMED"}
        intents = {r["idempotency_key"]: _decimal(r["quantity"], "exit intent")
                   for r in records if r["type"] == "EXIT_TRIGGERED"}
        cancelled: dict[str, Decimal] = {}
        for record in records:
            if record["type"] == "EXIT_CANCELLED":
                key = record["exit_key"]
                cancelled[key] = cancelled.get(key, Decimal(0)) + _decimal(record["quantity"], "cancel quantity")
            elif record["type"] == "EXIT_FILL":
                for cancellation in record.get("oco_cancelled_siblings", []):
                    key = cancellation["exit_key"]
                    cancelled[key] = cancelled.get(key, Decimal(0)) + _decimal(
                        cancellation["quantity"], "cancel quantity")
        fills: dict[str, Decimal] = {}
        for record in records:
            if record["type"] == "EXIT_FILL":
                fills[record["exit_key"]] = fills.get(record["exit_key"], Decimal(0)) + _decimal(
                    record["quantity"], "exit fill")
        entry_qty = sum(entries.values(), Decimal(0))
        exit_qty = sum(fills.values(), Decimal(0))
        remaining = {key: qty - fills.get(key, Decimal(0)) - cancelled.get(key, Decimal(0))
                     for key, qty in intents.items()}
        groups = {r["idempotency_key"]: r.get("oco_group_id") for r in records
                  if r.get("type") == "EXIT_TRIGGERED"}
        grouped_pending: dict[str, Decimal] = {}
        pending = Decimal(0)
        for key, quantity in remaining.items():
            group = groups.get(key)
            if group is None:
                pending += quantity
            else:
                grouped_pending[group] = max(grouped_pending.get(group, Decimal(0)), quantity)
        pending += sum(grouped_pending.values(), Decimal(0))
        unresolved = {r["intent_id"] for r in records if r["type"] == "EXIT_UNRESOLVED"}
        resolved = {r["resolves_unresolved_intent_id"] for r in records
                    if r["type"] == "EXIT_TRIGGERED" and r.get("resolves_unresolved_intent_id")}
        unresolved.difference_update(resolved)
        if any(fills.get(key, Decimal(0)) + cancelled.get(key, Decimal(0)) > qty
               for key, qty in intents.items()):
            raise ExitLedgerError("journal contains exit fills/cancellations above emitted intent")
        if exit_qty + pending > entry_qty:
            raise ExitLedgerError("journal contains exit intents/fills above entry quantity")
        return ExitLedgerState(
            entry_filled_quantity=entry_qty, exit_filled_quantity=exit_qty,
            exit_pending_quantity=pending, open_quantity=entry_qty - exit_qty,
            armed_protection_ids=frozenset(protections),
            emitted_idempotency_keys=frozenset(intents),
            unresolved_exit_intent_ids=frozenset(unresolved),
        )

    def record_entry_fill(self, *, entry_signal_id: str, fill_id: str, quantity: Decimal,
                          side: EntrySide, idempotency_key: str) -> bool:
        qty = _decimal(quantity, "entry quantity")
        if qty <= 0 or not fill_id.strip() or not idempotency_key.strip():
            raise ExitLedgerError("entry fill id, positive quantity and idempotency key are required")
        if not isinstance(side, EntrySide) or side is EntrySide.MF_SUBSCRIBE:
            raise ExitLedgerError("paper exchange exit protection requires an equity/options entry side")
        try:
            UUID(entry_signal_id)
        except (ValueError, AttributeError) as exc:
            raise ExitLedgerError("entry_signal_id must be a UUID") from exc
        record = {"type": "ENTRY_FILL", "position_id": self.position_id,
                  "entry_signal_id": entry_signal_id, "fill_id": fill_id,
                  "quantity": str(qty), "side": side.value, "idempotency_key": idempotency_key}
        records = self._position_records()
        prior_entries = [r for r in records if r.get("type") == "ENTRY_FILL"]
        if prior_entries and any(
            r["entry_signal_id"] != entry_signal_id or r["side"] != side.value for r in prior_entries
        ):
            raise ExitLedgerError("entry fills must retain one signal identity and side")
        prior_fill = next((r for r in records if r.get("type") == "ENTRY_FILL" and r.get("fill_id") == fill_id), None)
        if prior_fill is not None and prior_fill != record:
            raise ExitLedgerError("entry fill id conflicts with prior evidence")
        if prior_fill is None:
            planned_entry_quantity = sum(
                (_decimal(r["quantity"], "entry fill") for r in prior_entries), Decimal(0)
            ) + qty
            hard_stop_capacity = max((
                _decimal(r["quantity"], "hard-stop protection")
                for r in records if r.get("type") == "PROTECTION_ARMED"
                and r.get("trigger") == ExitTrigger.HARD_STOP.value
                and r.get("parent_entry_signal_id") == entry_signal_id
            ), default=Decimal(0))
            if planned_entry_quantity > hard_stop_capacity:
                raise ExitLedgerError("position entry requires a pre-armed hard-stop exit policy covering filled quantity")
        self._append_idempotent(self.journal, records, record, idempotency_key)
        _ = self.state
        return prior_fill is None

    def arm(self, protection: ExitProtectionIntent, *, idempotency_key: str) -> bool:
        if protection.position_id != self.position_id:
            raise ExitLedgerError("protection belongs to a different position")
        records = self._position_records()
        entry_records = [r for r in records if r["type"] == "ENTRY_FILL"]
        if entry_records and any(r["entry_signal_id"] != protection.parent_entry_signal_id for r in entry_records):
            raise ExitLedgerError("protection does not match the recorded entry signal")
        if entry_records and protection.quantity > self.state.open_quantity:
            raise ExitLedgerError("protected quantity exceeds open position quantity")
        record = {"type": "PROTECTION_ARMED", "position_id": self.position_id,
                  "protection_id": protection.protection_id,
                  "parent_entry_signal_id": protection.parent_entry_signal_id,
                  "trigger": protection.trigger.value, "trigger_price": str(protection.trigger_price),
                  "quantity": str(protection.quantity), "created_at": protection.created_at.isoformat(),
                  "source_hash": protection.source_hash, "oco_group_id": protection.oco_group_id,
                  "idempotency_key": idempotency_key}
        return self._append_idempotent(self.journal, records, record, idempotency_key)

    def emit(
        self,
        entry: EntrySignal,
        protection: ExitProtectionIntent,
        event: EmittedExitEvent,
        *,
        resolves_unresolved_intent_id: str | None = None,
    ) -> bool:
        if entry.opens_position_id != self.position_id:
            raise ExitLedgerError("entry belongs to a different position")
        records = self._position_records()
        state = self.state
        if protection.protection_id not in state.armed_protection_ids:
            raise ExitLedgerError("protection must be durably armed before trigger emission")
        if resolves_unresolved_intent_id is not None and (
            resolves_unresolved_intent_id not in state.unresolved_exit_intent_ids
        ):
            raise ExitLedgerError("cannot resolve an unknown or already resolved exit intent")
        if protection.oco_group_id is not None:
            group_exit_keys = {r["idempotency_key"] for r in records
                               if r.get("type") == "EXIT_TRIGGERED" and r.get("oco_group_id") == protection.oco_group_id}
            already_filled = any(r.get("type") == "EXIT_FILL" and r.get("exit_key") in group_exit_keys
                                 for r in records)
            if already_filled:
                raise ExitLedgerError("OCO sibling trigger is suppressed after the first sibling fill")
        key = event.idempotency_key
        record = {"type": "EXIT_TRIGGERED", "position_id": self.position_id,
                  "exit_signal_id": event.exit_signal.signal_id,
                  "protection_id": event.protection_id, "trigger": event.trigger.value,
                  "quantity": str(event.quantity), "occurred_at": event.exit_signal.occurred_at.isoformat(),
                  "observed_trigger_at": event.observed_trigger_at.isoformat(),
                  "source_hash": event.exit_signal.source_hash, "oco_group_id": event.oco_group_id,
                  "resolves_unresolved_intent_id": resolves_unresolved_intent_id,
                  "idempotency_key": key}
        prior = next((r for r in records if r.get("idempotency_key") == key), None)
        if prior is not None:
            if prior != record:
                raise ExitLedgerError("idempotency key conflicts with previously journaled evidence")
            return False
        already_reserved_oco_group = event.oco_group_id is not None and any(
            r.get("type") == "EXIT_TRIGGERED" and r.get("oco_group_id") == event.oco_group_id
            for r in records
        )
        if (not already_reserved_oco_group
                and event.quantity > state.open_quantity - state.exit_pending_quantity):
            raise ExitLedgerError("exit quantity exceeds unreserved open position quantity")
        try:
            validate_protection_emission(entry, protection, event, open_quantity=state.open_quantity)
        except PositionSignalError as exc:
            raise ExitLedgerError(str(exc)) from exc
        created = self._append_idempotent(self.journal, records, record, key)
        _ = self.state
        return created

    def record_unresolved_exit(
        self,
        *,
        intent_id: str,
        protection_id: str,
        trigger: ExitTrigger,
        quantity: Decimal,
        observed_at: datetime,
        reason: str,
        idempotency_key: str,
        evidence_hash: str | None = None,
    ) -> bool:
        """Durably retain a fired-but-unexecutable exit and expose it for operator alerting."""
        from uuid import UUID

        try:
            UUID(intent_id)
            UUID(protection_id)
        except (ValueError, AttributeError) as exc:
            raise ExitLedgerError("unresolved intent and protection IDs must be UUIDs") from exc
        qty = _decimal(quantity, "unresolved exit quantity")
        if qty <= 0 or not isinstance(trigger, ExitTrigger) or not reason.strip() or not idempotency_key.strip():
            raise ExitLedgerError("unresolved exit requires positive quantity, trigger, reason, and idempotency key")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ExitLedgerError("unresolved exit observed_at must be timezone-aware")
        if evidence_hash is not None and (len(evidence_hash) != 64 or any(c not in "0123456789abcdef" for c in evidence_hash)):
            raise ExitLedgerError("unresolved evidence_hash must be lowercase SHA-256")
        records = self._position_records()
        if protection_id not in {r["protection_id"] for r in records if r["type"] == "PROTECTION_ARMED"}:
            raise ExitLedgerError("unresolved exit protection must be durably armed")
        if qty > self.state.open_quantity:
            raise ExitLedgerError("unresolved exit quantity exceeds open position quantity")
        record = {"type": "EXIT_UNRESOLVED", "position_id": self.position_id,
                  "intent_id": intent_id, "protection_id": protection_id, "trigger": trigger,
                  "quantity": str(qty), "observed_at": observed_at.isoformat(), "reason": reason,
                  "evidence_hash": evidence_hash, "idempotency_key": idempotency_key}
        prior = [r for r in records if r.get("intent_id") == intent_id]
        if prior:
            if prior[0] != record:
                raise ExitLedgerError("unresolved intent ID conflicts with prior evidence")
            return False
        created = self._append_idempotent(self.journal, records, record, idempotency_key)
        return created

    def record_exit_fill(self, *, exit_key: str, fill_id: str, quantity: Decimal,
                         idempotency_key: str) -> bool:
        qty = _decimal(quantity, "exit fill quantity")
        if qty <= 0 or not fill_id.strip():
            raise ExitLedgerError("exit fill id and positive quantity are required")
        records = self._position_records()
        intents = {r["idempotency_key"]: _decimal(r["quantity"], "exit intent")
                   for r in records if r["type"] == "EXIT_TRIGGERED"}
        if exit_key not in intents:
            raise ExitLedgerError("exit fill references an unknown emitted exit")
        prior_fills = {r["fill_id"] for r in records if r["type"] == "EXIT_FILL"}
        if fill_id in prior_fills:
            prior = next(r for r in records if r.get("type") == "EXIT_FILL" and r.get("fill_id") == fill_id)
            if prior.get("exit_key") == exit_key and prior.get("quantity") == str(qty):
                return False
            raise ExitLedgerError("exit fill id conflicts with prior evidence")
        filled_for_exit = sum((_decimal(r["quantity"], "exit fill") for r in records
                               if r.get("type") == "EXIT_FILL" and r.get("exit_key") == exit_key), Decimal(0))
        cancelled_for_exit = sum((_decimal(r["quantity"], "exit cancellation") for r in records
                                  if r.get("type") == "EXIT_CANCELLED" and r.get("exit_key") == exit_key), Decimal(0))
        if filled_for_exit + cancelled_for_exit + qty > intents[exit_key]:
            raise ExitLedgerError("exit fill quantity exceeds emitted exit intent")
        record: dict[str, object] = {"type": "EXIT_FILL", "position_id": self.position_id, "exit_key": exit_key,
                                     "fill_id": fill_id, "quantity": str(qty),
                                     "idempotency_key": idempotency_key}
        prior_records = [r for r in records if r.get("idempotency_key") == idempotency_key]
        if prior_records:
            if prior_records[0] != record:
                raise ExitLedgerError("idempotency key conflicts with previously journaled evidence")
            return False
        sibling_cancellations: list[dict[str, str]] = []
        intent_record = next((r for r in records if r.get("type") == "EXIT_TRIGGERED"
                              and r.get("idempotency_key") == exit_key), None)
        if intent_record is None:
            raise ExitLedgerError("exit fill references an unknown emitted exit")
        group = intent_record.get("oco_group_id")
        if group is not None:
            group_intents = [r for r in records if r.get("type") == "EXIT_TRIGGERED"
                             and r.get("oco_group_id") == group and r.get("idempotency_key") != exit_key]
            for sibling in group_intents:
                sibling_key = sibling["idempotency_key"]
                sibling_filled = sum((_decimal(r["quantity"], "sibling fill") for r in records
                                      if r.get("type") == "EXIT_FILL" and r.get("exit_key") == sibling_key), Decimal(0))
                sibling_cancelled = sum((_decimal(r["quantity"], "sibling cancellation") for r in records
                                         if r.get("type") == "EXIT_CANCELLED" and r.get("exit_key") == sibling_key), Decimal(0))
                remainder = _decimal(sibling["quantity"], "sibling intent") - sibling_filled - sibling_cancelled
                if remainder > 0:
                    sibling_cancellations.append({"exit_key": sibling_key, "quantity": str(remainder)})
        # One hash-chained journal record makes the winner fill and OCO sibling
        # reductions inseparable during crash recovery.
        record["oco_cancelled_siblings"] = sibling_cancellations
        self.journal.append(record)
        _ = self.state
        return True


__all__ = ["ExitLedgerError", "ExitLedgerState", "ExitProtectionLedger"]
