"""Restart-safe paper exit protection reducer backed by the hash-chained journal."""
from __future__ import annotations

import hashlib
import json
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
from .exit_policy import ExitPolicy
from .exit_accounting import TrailingStopState


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
    closed_event_id: str | None


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
    def _decision_sha256(records: list[dict]) -> str:
        decisions = {row["decision_sha256"] for row in records
                     if row.get("type") == "ENTRY_SIGNAL" and row.get("decision_sha256")}
        if len(decisions) != 1:
            raise ExitLedgerError("position event requires exactly one immutable entry decision hash")
        return next(iter(decisions))

    @staticmethod
    def _append_idempotent(journal: DurableJournal, records: list[dict], record: dict, key: str) -> bool:
        if record.get("type") != "ENTRY_SIGNAL":
            record["decision_sha256"] = ExitProtectionLedger._decision_sha256(records)
        previous = next((r for r in records if r.get("idempotency_key") == key), None)
        if previous is not None:
            if previous != record:
                raise ExitLedgerError("idempotency key conflicts with previously journaled evidence")
            return False
        journal.append(record)
        return True

    @property
    def trailing_stop_state(self) -> TrailingStopState | None:
        """Recover the latest quote-derived trail from the hash-chained position journal."""
        record = next((row for row in reversed(self._position_records())
                       if row.get("type") == "TRAILING_STOP_UPDATE"), None)
        if record is None:
            return None
        return TrailingStopState(
            side=str(record["side"]), policy_version=str(record["policy_version"]),
            distance=Decimal(str(record["distance"])), watermark=Decimal(str(record["watermark"])),
            stop_price=Decimal(str(record["stop_price"])),
        )

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
            closed_event_id=next((r["event_id"] for r in records if r.get("type") == "POSITION_CLOSED"), None),
        )

    def record_entry_signal(self, entry: EntrySignal, *, snapshot: dict[str, object],
                            idempotency_key: str) -> bool:
        """Persist the entry decision and original snapshot before intent/fill."""
        if entry.opens_position_id != self.position_id:
            raise ExitLedgerError("entry signal belongs to a different position")
        if not idempotency_key.strip():
            raise ExitLedgerError("entry-signal idempotency key is required")
        try:
            snapshot_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ExitLedgerError("entry snapshot must be canonical JSON data") from exc
        record = {"type": "ENTRY_SIGNAL", "position_id": self.position_id,
                  "signal_id": entry.signal_id, "strategy_id": entry.strategy_id,
                  "strategy_version": entry.strategy_version, "instrument_id": entry.instrument_id,
                  "side": entry.side.value, "occurred_at": entry.occurred_at.isoformat(),
                  "quote_source_id": entry.quote_source_id, "source_hash": entry.source_hash,
                  "snapshot": json.loads(snapshot_json),
                  "snapshot_sha256": hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
                  "idempotency_key": idempotency_key}
        decision_manifest = {
            "signal_id": entry.signal_id,
            "position_id": self.position_id,
            "strategy_id": entry.strategy_id,
            "strategy_version": entry.strategy_version,
            "instrument_id": entry.instrument_id,
            "side": entry.side.value,
            "occurred_at": entry.occurred_at.isoformat(),
            "quote_source_id": entry.quote_source_id,
            "source_hash": entry.source_hash,
            "snapshot_sha256": record["snapshot_sha256"],
        }
        manifest_json = json.dumps(decision_manifest, sort_keys=True, separators=(",", ":"), allow_nan=False)
        record["decision_sha256"] = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
        records = self._position_records()
        if any(r.get("type") == "ENTRY_INTENT" and r.get("entry_signal_id") == entry.signal_id for r in records):
            raise ExitLedgerError("entry signal must be journaled before its intent")
        return self._append_idempotent(self.journal, records, record, idempotency_key)

    def record_entry_intent(self, *, entry_signal_id: str, intent_id: str, quantity: Decimal,
                            snapshot: dict[str, object], idempotency_key: str) -> bool:
        """Persist a decision-linked entry intent before accepting any fill."""
        qty = _decimal(quantity, "entry intent quantity")
        if qty <= 0 or not intent_id.strip() or not idempotency_key.strip():
            raise ExitLedgerError("entry intent requires identity, positive quantity and idempotency key")
        records = self._position_records()
        if not any(r.get("type") == "ENTRY_SIGNAL" and r.get("signal_id") == entry_signal_id for r in records):
            raise ExitLedgerError("entry intent requires a journaled ENTRY_SIGNAL")
        if any(r.get("type") == "ENTRY_FILL" and r.get("intent_id") == intent_id for r in records):
            raise ExitLedgerError("entry intent cannot be changed after a fill")
        try:
            snapshot_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ExitLedgerError("entry-intent snapshot must be canonical JSON data") from exc
        record = {"type": "ENTRY_INTENT", "position_id": self.position_id,
                  "entry_signal_id": entry_signal_id, "intent_id": intent_id, "quantity": str(qty),
                  "snapshot": json.loads(snapshot_json),
                  "snapshot_sha256": hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
                  "idempotency_key": idempotency_key}
        record["decision_sha256"] = self._decision_sha256(records)
        existing_intent = next((r for r in records if r.get("type") == "ENTRY_INTENT"
                                and r.get("intent_id") == intent_id), None)
        if existing_intent is not None:
            if existing_intent == record:
                return False
            raise ExitLedgerError("entry intent ID is already journaled with different content")
        return self._append_idempotent(self.journal, records, record, idempotency_key)

    def amend_entry_intent(self, *, intent_id: str, quantity: Decimal,
                           snapshot: dict[str, object], occurred_at: datetime,
                           source_hash: str, idempotency_key: str) -> bool:
        """Append a versioned, decision-linked entry amendment without rewriting the original intent."""
        qty = _decimal(quantity, "amended entry quantity")
        if qty <= 0 or not intent_id.strip() or not idempotency_key.strip():
            raise ExitLedgerError("entry amendment requires intent ID, positive quantity and idempotency key")
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ExitLedgerError("entry amendment time must be timezone-aware")
        if len(source_hash) != 64 or any(char not in "0123456789abcdef" for char in source_hash):
            raise ExitLedgerError("entry amendment source_hash must be lowercase SHA-256")
        try:
            snapshot_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ExitLedgerError("entry-amendment snapshot must be canonical JSON data") from exc
        records = self._position_records()
        original = next((r for r in records if r.get("type") == "ENTRY_INTENT"
                         and r.get("intent_id") == intent_id), None)
        if original is None:
            raise ExitLedgerError("entry amendment requires a journaled ENTRY_INTENT")
        signal = next((r for r in records if r.get("type") == "ENTRY_SIGNAL"
                       and r.get("signal_id") == original["entry_signal_id"]), None)
        if signal is None or occurred_at < datetime.fromisoformat(signal["occurred_at"]):
            raise ExitLedgerError("entry amendment cannot precede its frozen entry decision")
        prior_key = next((r for r in records if r.get("idempotency_key") == idempotency_key), None)
        if prior_key is not None:
            candidate = next((r for r in records if r.get("idempotency_key") == idempotency_key), None)
            if candidate is not None and candidate.get("type") == "ENTRY_INTENT_AMENDED" \
                    and candidate.get("intent_id") == intent_id and candidate.get("quantity") == str(qty) \
                    and candidate.get("snapshot") == json.loads(snapshot_json) \
                    and candidate.get("occurred_at") == occurred_at.isoformat() \
                    and candidate.get("source_hash") == source_hash:
                return False
            raise ExitLedgerError("entry amendment idempotency key conflicts with prior evidence")
        if any(r.get("type") in {"ENTRY_INTENT_CANCELLED", "ENTRY_INTENT_EXPIRED"}
               and r.get("intent_id") == intent_id for r in records):
            raise ExitLedgerError("terminal entry intent cannot be amended")
        fills = sum((_decimal(r["quantity"], "entry fill") for r in records
                     if r.get("type") == "ENTRY_FILL" and r.get("intent_id") == intent_id), Decimal(0))
        current_quantity = _decimal(
            max((r for r in records if r.get("type") == "ENTRY_INTENT_AMENDED"
                 and r.get("intent_id") == intent_id), key=lambda r: int(r["revision"]))["quantity"]
            if any(r.get("type") == "ENTRY_INTENT_AMENDED" and r.get("intent_id") == intent_id
                   for r in records) else original["quantity"], "current entry quantity")
        if fills >= current_quantity:
            raise ExitLedgerError("fully filled entry intent cannot be amended")
        if qty < fills:
            raise ExitLedgerError("amended entry quantity cannot be below already filled quantity")
        revisions = [r for r in records if r.get("type") == "ENTRY_INTENT_AMENDED"
                     and r.get("intent_id") == intent_id]
        if revisions and occurred_at < datetime.fromisoformat(max(
                revisions, key=lambda r: int(r["revision"]))["occurred_at"]):
            raise ExitLedgerError("entry amendment time cannot move backwards")
        revision = max((int(r["revision"]) for r in revisions), default=0) + 1
        record = {"type": "ENTRY_INTENT_AMENDED", "position_id": self.position_id,
                  "entry_signal_id": original["entry_signal_id"], "intent_id": intent_id,
                  "revision": revision, "quantity": str(qty), "snapshot": json.loads(snapshot_json),
                  "snapshot_sha256": hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
                  "occurred_at": occurred_at.isoformat(), "source_hash": source_hash,
                  "idempotency_key": idempotency_key,
                  "decision_sha256": self._decision_sha256(records)}
        self.journal.append(record)
        return True

    def cancel_entry_intent(self, *, intent_id: str, occurred_at: datetime,
                            source_hash: str, reason: str, idempotency_key: str) -> bool:
        """Persist a confirmed cancellation for the unfilled remainder of an entry intent."""
        return self._terminate_entry_intent(
            event_type="ENTRY_INTENT_CANCELLED", intent_id=intent_id, occurred_at=occurred_at,
            source_hash=source_hash, reason=reason, idempotency_key=idempotency_key)

    def expire_entry_intent(self, *, intent_id: str, occurred_at: datetime,
                            source_hash: str, reason: str, idempotency_key: str) -> bool:
        """Persist expiry only when the frozen intent snapshot's deadline has elapsed."""
        return self._terminate_entry_intent(
            event_type="ENTRY_INTENT_EXPIRED", intent_id=intent_id, occurred_at=occurred_at,
            source_hash=source_hash, reason=reason, idempotency_key=idempotency_key)

    def _terminate_entry_intent(self, *, event_type: str, intent_id: str, occurred_at: datetime,
                                source_hash: str, reason: str, idempotency_key: str) -> bool:
        if event_type not in {"ENTRY_INTENT_CANCELLED", "ENTRY_INTENT_EXPIRED"}:
            raise ExitLedgerError("unsupported entry terminal event")
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ExitLedgerError("entry terminal-event time must be timezone-aware")
        if (not intent_id.strip() or not reason.strip() or not idempotency_key.strip()
                or len(source_hash) != 64 or any(char not in "0123456789abcdef" for char in source_hash)):
            raise ExitLedgerError("entry terminal event requires intent, source hash, reason and idempotency key")
        records = self._position_records()
        original = next((r for r in records if r.get("type") == "ENTRY_INTENT"
                         and r.get("intent_id") == intent_id), None)
        if original is None:
            raise ExitLedgerError("entry terminal event requires a journaled ENTRY_INTENT")
        signal = next((r for r in records if r.get("type") == "ENTRY_SIGNAL"
                       and r.get("signal_id") == original["entry_signal_id"]), None)
        if signal is None or occurred_at < datetime.fromisoformat(signal["occurred_at"]):
            raise ExitLedgerError("entry terminal event cannot precede its frozen entry decision")
        prior_key = next((r for r in records if r.get("idempotency_key") == idempotency_key), None)
        if prior_key is not None:
            if (prior_key.get("type") == event_type and prior_key.get("intent_id") == intent_id
                    and prior_key.get("occurred_at") == occurred_at.isoformat()
                    and prior_key.get("source_hash") == source_hash and prior_key.get("reason") == reason):
                return False
            raise ExitLedgerError("entry terminal-event idempotency key conflicts with prior evidence")
        if any(r.get("type") in {"ENTRY_INTENT_CANCELLED", "ENTRY_INTENT_EXPIRED"}
               and r.get("intent_id") == intent_id for r in records):
            raise ExitLedgerError("entry intent is already terminal")
        amendments = [r for r in records if r.get("type") == "ENTRY_INTENT_AMENDED"
                      and r.get("intent_id") == intent_id]
        effective_quantity = _decimal(
            max(amendments, key=lambda r: int(r["revision"]))["quantity"] if amendments
            else original["quantity"], "effective entry quantity")
        filled = sum((_decimal(r["quantity"], "entry fill") for r in records
                      if r.get("type") == "ENTRY_FILL" and r.get("intent_id") == intent_id), Decimal(0))
        remaining = effective_quantity - filled
        if remaining <= 0:
            raise ExitLedgerError("fully filled entry intent cannot be cancelled or expired")
        if event_type == "ENTRY_INTENT_EXPIRED":
            latest_amendment = max(amendments, key=lambda r: int(r["revision"])) if amendments else None
            snapshot = latest_amendment["snapshot"] if latest_amendment else original["snapshot"]
            deadline_raw = snapshot.get("expires_at") if isinstance(snapshot, dict) else None
            try:
                deadline = datetime.fromisoformat(str(deadline_raw).replace("Z", "+00:00"))
            except (TypeError, ValueError) as exc:
                raise ExitLedgerError("entry expiry requires a frozen timezone-aware expires_at") from exc
            if deadline.tzinfo is None or deadline.utcoffset() is None or occurred_at < deadline:
                raise ExitLedgerError("entry intent cannot expire before its frozen deadline")
        record = {"type": event_type, "position_id": self.position_id,
                  "entry_signal_id": original["entry_signal_id"], "intent_id": intent_id,
                  "remaining_quantity": str(remaining), "occurred_at": occurred_at.isoformat(),
                  "source_hash": source_hash, "reason": reason,
                  "idempotency_key": idempotency_key,
                  "decision_sha256": self._decision_sha256(records)}
        return self._append_idempotent(self.journal, records, record, idempotency_key)

    def record_entry_fill(self, *, entry_signal_id: str, intent_id: str, fill_id: str, quantity: Decimal,
                          side: EntrySide, idempotency_key: str) -> bool:
        if self.state.closed_event_id is not None:
            raise ExitLedgerError("cannot add entry fills to a closed position")
        qty = _decimal(quantity, "entry quantity")
        if qty <= 0 or not fill_id.strip() or not intent_id.strip() or not idempotency_key.strip():
            raise ExitLedgerError("entry fill, intent, positive quantity and idempotency key are required")
        if not isinstance(side, EntrySide) or side is EntrySide.MF_SUBSCRIBE:
            raise ExitLedgerError("paper exchange exit protection requires an equity/options entry side")
        try:
            UUID(entry_signal_id)
        except (ValueError, AttributeError) as exc:
            raise ExitLedgerError("entry_signal_id must be a UUID") from exc
        record = {"type": "ENTRY_FILL", "position_id": self.position_id,
                  "entry_signal_id": entry_signal_id, "intent_id": intent_id, "fill_id": fill_id,
                  "quantity": str(qty), "side": side.value, "idempotency_key": idempotency_key}
        records = self._position_records()
        if not any(r.get("type") == "ENTRY_SIGNAL" and r.get("signal_id") == entry_signal_id for r in records):
            raise ExitLedgerError("entry fill requires a journaled ENTRY_SIGNAL")
        prior_entries = [r for r in records if r.get("type") == "ENTRY_FILL"]
        if prior_entries and any(
            r["entry_signal_id"] != entry_signal_id or r["side"] != side.value for r in prior_entries
        ):
            raise ExitLedgerError("entry fills must retain one signal identity and side")
        matching_intents = [r for r in records if r.get("type") == "ENTRY_INTENT"
                            and r.get("intent_id") == intent_id and r.get("entry_signal_id") == entry_signal_id]
        if not matching_intents:
            raise ExitLedgerError("entry fill requires a matching journaled ENTRY_INTENT")
        prior_fill = next((r for r in records if r.get("type") == "ENTRY_FILL" and r.get("fill_id") == fill_id), None)
        if prior_fill is not None:
            if prior_fill != record:
                raise ExitLedgerError("entry fill id conflicts with prior evidence")
            return False
        if any(r.get("type") in {"ENTRY_INTENT_CANCELLED", "ENTRY_INTENT_EXPIRED"}
               and r.get("intent_id") == intent_id for r in records):
            raise ExitLedgerError("terminal entry intent cannot receive later fills")
        if len(matching_intents) != 1:
            raise ExitLedgerError("entry intent identity must have exactly one original record")
        amendments = [r for r in records if r.get("type") == "ENTRY_INTENT_AMENDED"
                      and r.get("intent_id") == intent_id]
        latest_amendment = max(amendments, key=lambda r: int(r["revision"])) if amendments else None
        intent_qty = _decimal(latest_amendment["quantity"] if latest_amendment else
                              matching_intents[0]["quantity"], "effective entry intent")
        prior_intent_fills = sum((_decimal(r["quantity"], "entry fill") for r in records
                                  if r.get("type") == "ENTRY_FILL" and r.get("intent_id") == intent_id), Decimal(0))
        if prior_intent_fills + qty > intent_qty:
            raise ExitLedgerError("entry fill quantity exceeds matching entry intent")
        if prior_fill is None:
            planned_entry_quantity = sum(
                (_decimal(r["quantity"], "entry fill") for r in prior_entries), Decimal(0)
            ) + qty
            hard_stop_capacity = max((
                _decimal(r["quantity"], "hard-stop protection")
                for r in records if r.get("type") == "PROTECTION_ARMED"
                and r.get("trigger") == ExitTrigger.HARD_STOP.value
                and r.get("parent_entry_signal_id") == entry_signal_id
                and r.get("exit_policy_hash")
            ), default=Decimal(0))
            if planned_entry_quantity > hard_stop_capacity:
                raise ExitLedgerError("position entry requires a pre-armed hard-stop exit policy covering filled quantity")
        self._append_idempotent(self.journal, records, record, idempotency_key)
        _ = self.state
        return prior_fill is None

    def arm(self, protection: ExitProtectionIntent, *, exit_policy: ExitPolicy,
            idempotency_key: str) -> bool:
        if self.state.closed_event_id is not None:
            raise ExitLedgerError("cannot arm protection on a closed position")
        if protection.position_id != self.position_id:
            raise ExitLedgerError("protection belongs to a different position")
        records = self._position_records()
        entry_records = [r for r in records if r["type"] == "ENTRY_FILL"]
        signal = next((r for r in records if r.get("type") == "ENTRY_SIGNAL"
                       and r.get("signal_id") == protection.parent_entry_signal_id), None)
        if signal is None:
            raise ExitLedgerError("exit policy must bind to a journaled entry signal")
        if (exit_policy.strategy_id, exit_policy.strategy_version) != (
            signal["strategy_id"], signal["strategy_version"]
        ):
            raise ExitLedgerError("exit policy strategy identity differs from its entry signal")
        matching_rule = next((rule for rule in exit_policy.rules if rule.trigger is protection.trigger), None)
        if matching_rule is None or (
            matching_rule.trigger_price is not None and matching_rule.trigger_price != protection.trigger_price
        ):
            raise ExitLedgerError("protection intent is not covered by the versioned exit policy")
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
                  "exit_policy_version": exit_policy.policy_version,
                  "exit_policy_hash": exit_policy.policy_hash,
                  "idempotency_key": idempotency_key}
        return self._append_idempotent(self.journal, records, record, idempotency_key)

    def record_trailing_stop_update(self, *, state: TrailingStopState,
                                    observed_price: Decimal, observed_at: datetime,
                                    quote_event_id: str, quote_evidence_sha256: str,
                                    policy_sha256: str, idempotency_key: str,
                                    policy_change_reason: str | None = None) -> bool:
        """Persist a quote-derived monotone trail in the position's hash-chained journal."""
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ExitLedgerError("trailing-stop observation time must be timezone-aware")
        if not quote_event_id.strip() or not idempotency_key.strip():
            raise ExitLedgerError("trailing-stop quote and idempotency identities are required")
        for label, digest in (("quote evidence", quote_evidence_sha256), ("policy", policy_sha256)):
            if (len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)):
                raise ExitLedgerError(f"{label} hash must be lowercase SHA-256")
        price = _decimal(observed_price, "trailing-stop observed price")
        if price <= 0:
            raise ExitLedgerError("trailing-stop observed price must be positive")
        records = self._position_records()
        entry = next((row for row in records if row.get("type") == "ENTRY_SIGNAL"), None)
        if entry is None:
            raise ExitLedgerError("trailing stop requires a journaled entry signal")
        if state.side != entry["side"]:
            raise ExitLedgerError("trailing-stop direction differs from the position entry")
        record = {"type": "TRAILING_STOP_UPDATE", "position_id": self.position_id,
                  "side": state.side, "policy_version": state.policy_version,
                  "policy_sha256": policy_sha256, "distance": str(state.distance),
                  "watermark": str(state.watermark), "stop_price": str(state.stop_price),
                  "observed_price": str(price), "observed_at": observed_at.isoformat(),
                  "quote_event_id": quote_event_id, "quote_evidence_sha256": quote_evidence_sha256,
                  "policy_change_reason": policy_change_reason, "idempotency_key": idempotency_key,
                  "decision_sha256": self._decision_sha256(records)}
        prior_key = next((row for row in records if row.get("idempotency_key") == idempotency_key), None)
        if prior_key is not None:
            if prior_key == record:
                return False
            raise ExitLedgerError("idempotency key conflicts with previously journaled evidence")
        duplicate_quote = next((row for row in records if row.get("type") == "TRAILING_STOP_UPDATE"
                                and row.get("quote_event_id") == quote_event_id), None)
        if duplicate_quote is not None:
            if {key: value for key, value in duplicate_quote.items() if key != "decision_sha256"} == {
                    key: value for key, value in record.items() if key != "decision_sha256"}:
                return False
            raise ExitLedgerError("quote event already has a conflicting trailing-stop update")
        position_state = self.state
        if position_state.open_quantity <= 0 or position_state.closed_event_id is not None:
            raise ExitLedgerError("trailing-stop updates require an open filled position")
        latest_entry_fill = max((datetime.fromisoformat(str(row["occurred_at"])) for row in records
                                 if row.get("type") == "ENTRY_FILL" and row.get("occurred_at")),
                                default=None)
        if latest_entry_fill is not None and observed_at.astimezone(latest_entry_fill.tzinfo) < latest_entry_fill:
            raise ExitLedgerError("trailing-stop observation cannot precede an entry fill")
        prior = next((row for row in reversed(records) if row.get("type") == "TRAILING_STOP_UPDATE"), None)
        if prior is not None:
            prior_at = datetime.fromisoformat(str(prior["observed_at"]))
            if observed_at.astimezone(prior_at.tzinfo) <= prior_at:
                raise ExitLedgerError("trailing-stop observations must advance in time")
            changed_policy = state.policy_version != prior["policy_version"]
            if changed_policy:
                if not isinstance(policy_change_reason, str) or not policy_change_reason.strip():
                    raise ExitLedgerError("trailing-stop policy change requires an explicit reason")
                if state.watermark != price:
                    raise ExitLedgerError("new trailing-stop policy must start from the current observed quote")
            else:
                if policy_change_reason is not None:
                    raise ExitLedgerError("policy-change reason is only valid when the version changes")
                if policy_sha256 != prior["policy_sha256"] or state.distance != Decimal(prior["distance"]):
                    raise ExitLedgerError("trailing-stop policy hash/distance changed without a version change")
                old_watermark = Decimal(prior["watermark"])
                expected_watermark = (max(old_watermark, price) if state.side == "BUY"
                                      else min(old_watermark, price))
                if state.watermark != expected_watermark:
                    raise ExitLedgerError("trailing-stop watermark does not follow the observed quote")
        else:
            if policy_change_reason is not None:
                raise ExitLedgerError("initial trailing-stop state cannot carry a policy-change reason")
            if state.watermark != price:
                raise ExitLedgerError("initial trailing-stop watermark must equal the observed quote")
        expected_stop = (state.watermark - state.distance if state.side == "BUY"
                         else state.watermark + state.distance)
        if state.stop_price != expected_stop or expected_stop <= 0:
            raise ExitLedgerError("trailing-stop price must match its observed watermark and distance")
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
        key = event.idempotency_key
        record = {"type": "EXIT_TRIGGERED", "position_id": self.position_id,
                  "exit_signal_id": event.exit_signal.signal_id,
                  "protection_id": event.protection_id, "trigger": event.trigger.value,
                  "quantity": str(event.quantity), "occurred_at": event.exit_signal.occurred_at.isoformat(),
                  "observed_trigger_at": event.observed_trigger_at.isoformat(),
                  "source_hash": event.exit_signal.source_hash, "oco_group_id": event.oco_group_id,
                  "resolves_unresolved_intent_id": resolves_unresolved_intent_id,
                  "idempotency_key": key}
        record["decision_sha256"] = self._decision_sha256(records)
        prior = next((r for r in records if r.get("idempotency_key") == key), None)
        if prior is not None:
            if prior != record:
                raise ExitLedgerError("idempotency key conflicts with previously journaled evidence")
            return False
        if state.closed_event_id is not None:
            raise ExitLedgerError("cannot emit exits for a closed position")
        if state.unresolved_exit_intent_ids and resolves_unresolved_intent_id is None:
            raise ExitLedgerError("an unresolved exit must be explicitly linked when emitting its executable exit")
        if protection.protection_id not in state.armed_protection_ids:
            raise ExitLedgerError("protection must be durably armed before trigger emission")
        if resolves_unresolved_intent_id is not None and (
            resolves_unresolved_intent_id not in state.unresolved_exit_intent_ids
        ):
            raise ExitLedgerError("cannot resolve an unknown or already resolved exit intent")
        if resolves_unresolved_intent_id is not None:
            unresolved_record = next((r for r in records if r.get("type") == "EXIT_UNRESOLVED"
                                      and r.get("intent_id") == resolves_unresolved_intent_id), None)
            if unresolved_record is None or unresolved_record.get("protection_id") != protection.protection_id:
                raise ExitLedgerError("executable exit must match the unresolved protection")
            if event.quantity > _decimal(unresolved_record["quantity"], "unresolved quantity"):
                raise ExitLedgerError("executable exit exceeds unresolved quantity")
        if protection.oco_group_id is not None:
            group_exit_keys = {r["idempotency_key"] for r in records
                               if r.get("type") == "EXIT_TRIGGERED" and r.get("oco_group_id") == protection.oco_group_id}
            already_filled = any(r.get("type") == "EXIT_FILL" and r.get("exit_key") in group_exit_keys
                                 for r in records)
            if already_filled:
                raise ExitLedgerError("OCO sibling trigger is suppressed after the first sibling fill")
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
        protection_record = next((r for r in records if r.get("type") == "PROTECTION_ARMED"
                                  and r.get("protection_id") == protection_id), None)
        if protection_record is None:
            raise ExitLedgerError("unresolved exit protection must be durably armed")
        if protection_record.get("trigger") != trigger.value or qty > _decimal(
            protection_record["quantity"], "protected quantity"
        ):
            raise ExitLedgerError("unresolved exit must match trigger and protected quantity")
        if qty > self.state.open_quantity:
            raise ExitLedgerError("unresolved exit quantity exceeds open position quantity")
        record = {"type": "EXIT_UNRESOLVED", "position_id": self.position_id,
                  "intent_id": intent_id, "protection_id": protection_id, "trigger": trigger,
                  "quantity": str(qty), "observed_at": observed_at.isoformat(), "reason": reason,
                  "evidence_hash": evidence_hash, "idempotency_key": idempotency_key}
        record["decision_sha256"] = self._decision_sha256(records)
        prior = [r for r in records if r.get("intent_id") == intent_id]
        if prior:
            if prior[0] != record:
                raise ExitLedgerError("unresolved intent ID conflicts with prior evidence")
            return False
        created = self._append_idempotent(self.journal, records, record, idempotency_key)
        return created

    def record_exit_execution(self, *, exit_key: str, execution_id: str, simulated_at: datetime,
                              quote_event_id: str, quote_observed_at: datetime,
                              quote_available_at: datetime, quote_evidence_sha256: str,
                              idempotency_key: str) -> bool:
        """Persist a paper execution attempt separately from trigger and fill clocks."""
        clocks = (simulated_at, quote_observed_at, quote_available_at)
        if any(value.tzinfo is None or value.utcoffset() is None for value in clocks):
            raise ExitLedgerError("paper execution and quote clocks must be timezone-aware")
        if not execution_id.strip() or not quote_event_id.strip() or not idempotency_key.strip():
            raise ExitLedgerError("execution, quote and idempotency identities are required")
        if (len(quote_evidence_sha256) != 64 or
                any(char not in "0123456789abcdef" for char in quote_evidence_sha256)):
            raise ExitLedgerError("quote evidence must be lowercase SHA-256")
        records = self._position_records()
        trigger = next((row for row in records if row.get("type") == "EXIT_TRIGGERED"
                        and row.get("idempotency_key") == exit_key), None)
        if trigger is None:
            raise ExitLedgerError("paper execution references an unknown emitted exit")
        trigger_at = datetime.fromisoformat(str(trigger["observed_trigger_at"]))
        emitted_at = datetime.fromisoformat(str(trigger["occurred_at"]))
        if simulated_at < max(trigger_at, emitted_at):
            raise ExitLedgerError("paper execution cannot precede the observed exit trigger")
        if quote_available_at < quote_observed_at or quote_available_at > simulated_at:
            raise ExitLedgerError("paper execution quote must be observed and available by execution time")
        record = {"type": "EXIT_EXECUTION", "position_id": self.position_id,
                  "exit_key": exit_key, "execution_id": execution_id,
                  "simulated_at": simulated_at.isoformat(), "quote_event_id": quote_event_id,
                  "quote_observed_at": quote_observed_at.isoformat(),
                  "quote_available_at": quote_available_at.isoformat(),
                  "quote_evidence_sha256": quote_evidence_sha256,
                  "idempotency_key": idempotency_key}
        record["decision_sha256"] = self._decision_sha256(records)
        prior_id = next((row for row in records if row.get("type") == "EXIT_EXECUTION"
                         and row.get("execution_id") == execution_id), None)
        if prior_id is not None:
            if prior_id != record:
                raise ExitLedgerError("execution ID conflicts with prior evidence")
            return False
        return self._append_idempotent(self.journal, records, record, idempotency_key)

    def record_exit_fill(self, *, exit_key: str, execution_id: str, fill_id: str, quantity: Decimal,
                         occurred_at: datetime, price: Decimal, idempotency_key: str) -> bool:
        qty = _decimal(quantity, "exit fill quantity")
        fill_price = _decimal(price, "exit fill price")
        if qty <= 0 or fill_price <= 0 or not fill_id.strip() or not execution_id.strip():
            raise ExitLedgerError("exit fill, execution IDs, positive quantity and price are required")
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ExitLedgerError("observed fill time must be timezone-aware")
        records = self._position_records()
        execution = next((row for row in records if row.get("type") == "EXIT_EXECUTION"
                          and row.get("execution_id") == execution_id and row.get("exit_key") == exit_key), None)
        if execution is None:
            raise ExitLedgerError("exit fill requires a matching persisted paper execution")
        simulated_at = datetime.fromisoformat(str(execution["simulated_at"]))
        if occurred_at < simulated_at:
            raise ExitLedgerError("observed fill cannot precede paper execution time")
        record: dict[str, object] = {"type": "EXIT_FILL", "position_id": self.position_id,
                                     "exit_key": exit_key, "execution_id": execution_id,
                                     "fill_id": fill_id, "quantity": str(qty),
                                     "price": str(fill_price), "occurred_at": occurred_at.isoformat(),
                                     "idempotency_key": idempotency_key,
                                     "oco_cancelled_siblings": []}
        record["decision_sha256"] = self._decision_sha256(records)
        prior_fill = next((r for r in records if r.get("type") == "EXIT_FILL" and r.get("fill_id") == fill_id), None)
        if prior_fill is not None:
            # OCO sibling reductions are derived atomically from the winning fill and
            # are persisted only on the original event. Compare the supplied fill facts
            # exactly while preserving that immutable derived cancellation evidence.
            if ({key: value for key, value in prior_fill.items() if key != "oco_cancelled_siblings"} ==
                    {key: value for key, value in record.items() if key != "oco_cancelled_siblings"}):
                return False
            raise ExitLedgerError("exit fill id conflicts with prior evidence")
        if self.state.closed_event_id is not None:
            raise ExitLedgerError("cannot add exit fills to a closed position")
        if qty > self.state.open_quantity:
            raise ExitLedgerError("exit fill quantity exceeds open position quantity")
        intents = {r["idempotency_key"]: _decimal(r["quantity"], "exit intent")
                   for r in records if r["type"] == "EXIT_TRIGGERED"}
        if exit_key not in intents:
            raise ExitLedgerError("exit fill references an unknown emitted exit")
        filled_for_exit = sum((_decimal(r["quantity"], "exit fill") for r in records
                               if r.get("type") == "EXIT_FILL" and r.get("exit_key") == exit_key), Decimal(0))
        cancelled_for_exit = sum((_decimal(r["quantity"], "exit cancellation") for r in records
                                  if r.get("type") == "EXIT_CANCELLED" and r.get("exit_key") == exit_key), Decimal(0))
        if filled_for_exit + cancelled_for_exit + qty > intents[exit_key]:
            raise ExitLedgerError("exit fill quantity exceeds emitted exit intent")
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
        record["decision_sha256"] = self._decision_sha256(records)
        self.journal.append(record)
        _ = self.state
        return True

    def record_position_closed(self, *, event_id: str, reason: str, occurred_at: datetime,
                               idempotency_key: str) -> bool:
        """Append a closure only after recorded fills close all open and pending risk."""
        try:
            UUID(event_id)
        except (ValueError, AttributeError) as exc:
            raise ExitLedgerError("position close event_id must be a UUID") from exc
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ExitLedgerError("position close time must be timezone-aware")
        if not reason.strip() or not idempotency_key.strip():
            raise ExitLedgerError("position close requires reason and idempotency key")
        latest_fill = max((datetime.fromisoformat(str(row["occurred_at"])) for row in self._position_records()
                           if row.get("type") == "EXIT_FILL" and row.get("occurred_at")), default=None)
        if latest_fill is not None and occurred_at < latest_fill:
            raise ExitLedgerError("position accounting close cannot precede its latest observed exit fill")
        state = self.state
        if state.entry_filled_quantity <= 0 or state.open_quantity != 0:
            raise ExitLedgerError("position cannot close before recorded fills close its quantity")
        if state.exit_pending_quantity > 0 or state.unresolved_exit_intent_ids:
            raise ExitLedgerError("position cannot close with pending or unresolved exits")
        if state.closed_event_id is not None:
            records = self._position_records()
            existing = next((r for r in records if r.get("type") == "POSITION_CLOSED"), None)
            candidate = {"type": "POSITION_CLOSED", "position_id": self.position_id,
                         "event_id": event_id, "occurred_at": occurred_at.isoformat(), "reason": reason,
                         "entry_filled_quantity": str(state.entry_filled_quantity),
                         "exit_filled_quantity": str(state.exit_filled_quantity),
                         "idempotency_key": idempotency_key,
                         "decision_sha256": self._decision_sha256(records)}
            if existing == candidate:
                return False
            raise ExitLedgerError("position already has a different close event")
        record = {"type": "POSITION_CLOSED", "position_id": self.position_id,
                  "event_id": event_id, "occurred_at": occurred_at.isoformat(), "reason": reason,
                  "entry_filled_quantity": str(state.entry_filled_quantity),
                  "exit_filled_quantity": str(state.exit_filled_quantity),
                  "idempotency_key": idempotency_key}
        return self._append_idempotent(self.journal, self._position_records(), record, idempotency_key)


__all__ = ["ExitLedgerError", "ExitLedgerState", "ExitProtectionLedger"]
