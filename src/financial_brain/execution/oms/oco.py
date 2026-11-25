"""Replay-safe OCO state transitions over the append-only forward order journal."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from financial_brain.forward_edge.order_journal import OrderEvent, OrderJournal, OrderJournalError


@dataclass(frozen=True)
class OCOResult:
    group_id: str
    event_id: str
    event: Literal["ARMED", "CANCEL_REQUESTED", "CANCEL_CONFIRMED", "SIBLING_FILL_RACE", "REPLAY"]
    triggered_order_id: str | None
    sibling_order_id: str | None
    sibling_state: str | None
    warning: str | None


def _event_key(group_id: str, event: str, order_id: str | None, source_event_id: str | None) -> str:
    raw = "|".join((group_id, event, order_id or "", source_event_id or ""))
    return hashlib.sha256(raw.encode()).hexdigest()


def arm_oco(journal: OrderJournal, *, group_id: str, stop_order_id: str, target_order_id: str,
            occurred_at: datetime, evidence_id: str) -> OCOResult:
    """Create a hash-pinned OCO relationship once; repeated identical requests replay safely."""
    if not group_id.strip() or stop_order_id == target_order_id:
        raise OrderJournalError("OCO group and distinct child order IDs are required")
    stop = journal.replay(stop_order_id)
    target = journal.replay(target_order_id)
    if not stop or not target:
        raise OrderJournalError("both OCO children must already exist in the durable order journal")
    _validate_evidence(evidence_id)
    event_id = _event_key(group_id, "ARMED", None, evidence_id)
    payload = {"group_id": group_id, "stop_order_id": stop_order_id, "target_order_id": target_order_id,
               "evidence_id": evidence_id, "occurred_at": occurred_at.isoformat()}
    return _append_control(journal, order_id=f"oco:{group_id}", event_id=event_id,
                           state="CREATED", occurred_at=occurred_at, payload=payload,
                           output_event="ARMED", group_id=group_id)


def resolve_oco_fill(journal: OrderJournal, *, group_id: str, triggered_order_id: str,
                     fill_event_id: str, occurred_at: datetime, reconciliation_evidence_id: str) -> OCOResult:
    """Cancel the sibling after a child fill, but never erase a late sibling fill race.

    Journal child state is authoritative. A cancel request does not cancel the sibling;
    the sibling must later receive a CANCELED event. If it fills meanwhile, the group is
    marked SIBLING_FILL_RACE and requires position reconciliation/flattening.
    """
    control_id = f"oco:{group_id}"
    control = journal.replay(control_id)
    if not control:
        raise OrderJournalError("OCO group is not armed")
    body = control[0]
    payload = body["accounting"]
    stop_id, target_id = payload["stop_order_id"], payload["target_order_id"]
    if triggered_order_id not in (stop_id, target_id):
        raise OrderJournalError("filled order is not an OCO child")
    child = journal.replay(triggered_order_id)
    if not child or child[-1]["state"] != "FILLED" or child[-1]["event_id"] != fill_event_id:
        raise OrderJournalError("triggered OCO child lacks the exact durable fill event")
    _validate_evidence(reconciliation_evidence_id)
    sibling_id = target_id if triggered_order_id == stop_id else stop_id
    sibling_events = journal.replay(sibling_id)
    sibling_state = sibling_events[-1]["state"]
    if sibling_state == "FILLED":
        event = "SIBLING_FILL_RACE"
        warning = "both OCO children filled; reconcile exposure and cancel/flatten residual position"
    elif sibling_state == "CANCELED":
        event = "CANCEL_CONFIRMED"
        warning = None
    else:
        event = "CANCEL_REQUESTED"
        warning = "sibling remains live until cancellation is confirmed; late fill can race"
    state_map = {"SIBLING_FILL_RACE": "UNKNOWN", "CANCEL_CONFIRMED": "CANCELED",
                 "CANCEL_REQUESTED": "CANCEL_PENDING"}
    current_group_state = control[-1]["state"]
    control_last = control[-1]
    seq = int(control_last["sequence"]) + 1
    if current_group_state == "CREATED":
        journal.append(OrderEvent(control_id, _event_key(group_id, "WORKING", None, None), seq,
                                  "SUBMITTED", occurred_at, 1, 0, None, payload))
        seq += 1
        journal.append(OrderEvent(control_id, _event_key(group_id, "ACCEPTED", None, None), seq,
                                  "ACCEPTED", occurred_at, 1, 0, None, payload))
        seq += 1
    elif current_group_state == "SUBMITTED":
        journal.append(OrderEvent(control_id, _event_key(group_id, "ACCEPTED", None, None), seq,
                                  "ACCEPTED", occurred_at, 1, 0, None, payload))
        seq += 1
    event_id = _event_key(group_id, event, triggered_order_id, fill_event_id)
    order_event = OrderEvent(
        order_id=control_id, event_id=event_id, sequence=seq, state=state_map[event],
        occurred_at=occurred_at, quantity=1, filled_quantity=0, price=None,
        accounting={"group_id": group_id, "stop_order_id": stop_id, "target_order_id": target_id,
                    "triggered_order_id": triggered_order_id,
                    "fill_event_id": fill_event_id, "sibling_order_id": sibling_id,
                    "sibling_state": sibling_state, "event": event,
                    "reconciliation_evidence_id": reconciliation_evidence_id},
        reconciliation_evidence_id=reconciliation_evidence_id)
    replay = journal.replay(control_id)
    if any(row["event_id"] == event_id for row in replay):
        return OCOResult(group_id, event_id, "REPLAY", triggered_order_id, sibling_id, sibling_state, warning)
    journal.append(order_event)
    return OCOResult(group_id, event_id, cast(Literal["CANCEL_REQUESTED", "CANCEL_CONFIRMED", "SIBLING_FILL_RACE"], event),
                     triggered_order_id, sibling_id, sibling_state, warning)


def _append_control(journal: OrderJournal, *, order_id: str, event_id: str, state: str,
                    occurred_at: datetime, payload: dict, output_event: str,
                    group_id: str) -> OCOResult:
    existing = journal.replay(order_id)
    if existing:
        if existing[0]["event_id"] == event_id and existing[0]["accounting"] == payload:
            return OCOResult(group_id, event_id, "REPLAY", None, None, None, None)
        raise OrderJournalError("OCO group already exists with conflicting children or evidence")
    journal.append(OrderEvent(order_id, event_id, 0, state, occurred_at, 1, 0, None, payload))
    return OCOResult(group_id, event_id, cast(Literal["ARMED"], output_event), None, None, None, None)


def _validate_evidence(value: str) -> None:
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError("OCO evidence must be a lowercase SHA-256 digest")

