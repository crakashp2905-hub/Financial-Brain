"""Exact simulated-paper order lifecycle projection from durable receipts.

An execution adapter may return an ordered ``order_updates`` list to retain working,
partial, cancel, and missed-fill events. A terminal receipt still yields one explicit
snapshot when the adapter has no event history. Missing intended quantity remains
unknown; it is never inferred from the quantity filled.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime


class PaperOrderLifecycleError(ValueError):
    """Malformed or contradictory simulated order lifecycle evidence."""


ORDER_STATES = frozenset({
    "CREATED", "SUBMITTED", "PARTIAL", "FILLED", "CANCEL_PENDING",
    "CANCELED", "MISSED", "REJECTED", "NO_TRADE", "UNKNOWN",
})


def _quantity(value: object, name: str, *, allow_unknown: bool = False) -> int | None:
    if value is None and allow_unknown:
        return None
    if type(value) is not int or value < 0:
        raise PaperOrderLifecycleError(f"{name} must be a non-negative integer")
    return value


def normalize_order_update(*, order_id: str, update: dict, update_index: int,
                           receipt_id: str | None = None) -> dict:
    """Validate and seal one lifecycle update suitable for an immediate journal append."""
    if not isinstance(order_id, str) or not order_id:
        raise PaperOrderLifecycleError("stable paper order id is required")
    if not isinstance(update, dict):
        raise PaperOrderLifecycleError("order update must be an object")
    status = update.get("status")
    if status not in ORDER_STATES:
        raise PaperOrderLifecycleError(f"unrecognized order update status {status!r}")
    update_id = update.get("update_id")
    if not isinstance(update_id, str) or not update_id.strip():
        raise PaperOrderLifecycleError("each order update needs a stable update_id")
    try:
        at = datetime.fromisoformat(update.get("at_utc", ""))
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise PaperOrderLifecycleError("each order update needs an aware at_utc timestamp") from exc
    requested = _quantity(update.get("requested_quantity"), "update requested quantity", allow_unknown=True)
    filled = _quantity(update.get("filled_quantity"), "update filled quantity")
    remaining = _quantity(update.get("remaining_quantity"), "update remaining quantity", allow_unknown=True)
    if requested is not None:
        if requested <= 0 or filled > requested:
            raise PaperOrderLifecycleError("order update quantity totals are invalid")
        if remaining != requested - filled:
            raise PaperOrderLifecycleError("remaining quantity must equal requested minus filled")
    elif remaining is not None:
        raise PaperOrderLifecycleError("remaining quantity cannot be known without requested quantity")
    if type(update_index) is not int or update_index < 0:
        raise PaperOrderLifecycleError("order update sequence must be non-negative")
    body = {
        "t": "PAPER_ORDER_UPDATE", "order_id": order_id, "update_id": update_id,
        "status": status, "at_utc": at.isoformat(),
        "requested_quantity": requested, "filled_quantity": filled,
        "remaining_quantity": remaining, "receipt_id": receipt_id,
        "reason": update.get("reason"), "update_index": update_index,
    }
    body["update_sha256"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return body


def updates_for_receipt(*, order_id: str, decision: dict, receipt: dict,
                        starting_index: int = 0) -> list[dict]:
    """Validate adapter lifecycle evidence or create a conservative terminal snapshot."""
    if not isinstance(order_id, str) or not order_id:
        raise PaperOrderLifecycleError("stable paper order id is required")
    if not isinstance(decision, dict) or not isinstance(receipt, dict):
        raise PaperOrderLifecycleError("decision and execution receipt must be objects")
    status = receipt.get("status", receipt.get("state"))
    if status not in ORDER_STATES:
        raise PaperOrderLifecycleError(f"unrecognized paper order status {status!r}")
    fills = receipt.get("fills", []) or []
    if not isinstance(fills, list):
        raise PaperOrderLifecycleError("execution fills must be a list")
    filled = 0
    for fill in fills:
        if not isinstance(fill, dict):
            raise PaperOrderLifecycleError("each fill must be an object")
        filled += _quantity(fill.get("quantity"), "fill quantity") or 0
    requested_raw = decision.get("quantity", decision.get("requested_quantity"))
    if requested_raw is None:
        requested_raw = receipt.get("requested_quantity")
    requested = _quantity(requested_raw, "requested quantity", allow_unknown=True)
    if requested is not None and requested <= 0:
        raise PaperOrderLifecycleError("requested quantity must be positive")
    if requested is not None and filled > requested:
        raise PaperOrderLifecycleError("cumulative fills exceed requested quantity")
    remaining = requested - filled if requested is not None else None

    raw_updates = receipt.get("order_updates")
    if raw_updates is None:
        raw_updates = [{
            "update_id": f"receipt:{receipt.get('receipt_id', 'unknown')}",
            "status": status,
            "at_utc": receipt.get("simulated_execution_at_utc"),
            "requested_quantity": requested,
            "filled_quantity": filled,
            "remaining_quantity": remaining,
            "reason": receipt.get("reason"),
        }]
    if not isinstance(raw_updates, list) or not raw_updates:
        raise PaperOrderLifecycleError("order_updates must be a non-empty ordered list")

    out: list[dict] = []
    previous_filled = 0
    for index, raw in enumerate(raw_updates):
        if not isinstance(raw, dict):
            raise PaperOrderLifecycleError("each order update must be an object")
        update_status = raw.get("status")
        if update_status not in ORDER_STATES:
            raise PaperOrderLifecycleError(f"unrecognized order update status {update_status!r}")
        update_id = raw.get("update_id")
        if not isinstance(update_id, str) or not update_id.strip():
            raise PaperOrderLifecycleError("each order update needs a stable update_id")
        update_requested = _quantity(raw.get("requested_quantity", requested),
                                     "update requested quantity", allow_unknown=True)
        update_filled = _quantity(raw.get("filled_quantity"), "update filled quantity")
        update_remaining = _quantity(raw.get("remaining_quantity", None),
                                      "update remaining quantity", allow_unknown=True)
        if update_filled < previous_filled:
            raise PaperOrderLifecycleError("cumulative order fills cannot decrease")
        if update_requested is not None:
            if update_requested <= 0 or update_filled > update_requested:
                raise PaperOrderLifecycleError("order update quantity totals are invalid")
            expected_remaining = update_requested - update_filled
            if update_remaining != expected_remaining:
                raise PaperOrderLifecycleError("remaining quantity must equal requested minus filled")
        elif update_remaining is not None:
            raise PaperOrderLifecycleError("remaining quantity cannot be known without requested quantity")
        body = normalize_order_update(
            order_id=order_id,
            update={**raw, "requested_quantity": update_requested,
                    "filled_quantity": update_filled, "remaining_quantity": update_remaining},
            update_index=starting_index + index, receipt_id=receipt.get("receipt_id"),
        )
        out.append(body)
        previous_filled = update_filled
    final = out[-1]
    if final["filled_quantity"] != filled or final["status"] != status:
        raise PaperOrderLifecycleError("last order update must reconcile to the execution receipt")
    if requested is not None and final["remaining_quantity"] != remaining:
        raise PaperOrderLifecycleError("last order update does not reconcile requested and filled quantity")
    return out


def project_order_updates(records: list[dict], *, session_id: str | None = None) -> list[dict]:
    """Fold immutable journal updates into exact latest order rows for a read-only UI."""
    latest: dict[str, dict] = {}
    seen: dict[tuple[str, str], dict] = {}
    next_index: dict[str, int] = {}
    for record in records:
        if record.get("t") != "PAPER_ORDER_UPDATE":
            continue
        if session_id is not None and record.get("session_id") != session_id:
            continue
        order_id = record.get("order_id")
        update_id = record.get("update_id")
        if not isinstance(order_id, str) or not isinstance(update_id, str):
            raise PaperOrderLifecycleError("journal order update has no stable identity")
        body = {key: value for key, value in record.items()
                if key not in {"session_id", "update_sha256"}}
        expected_hash = hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        if record.get("update_sha256") != expected_hash:
            raise PaperOrderLifecycleError("paper order update content hash does not match its fields")
        key = (order_id, update_id)
        prior = seen.get(key)
        if prior is not None and prior != record:
            raise PaperOrderLifecycleError("order update identity was reused with different contents")
        seen[key] = record
        if prior is None:
            expected_index = next_index.get(order_id, 0)
            if record.get("update_index") != expected_index:
                raise PaperOrderLifecycleError("order update sequence has a gap or duplicate")
            next_index[order_id] = expected_index + 1
            normalize_order_update(
                order_id=order_id,
                update={
                    "update_id": update_id, "status": record.get("status"),
                    "at_utc": record.get("at_utc"),
                    "requested_quantity": record.get("requested_quantity"),
                    "filled_quantity": record.get("filled_quantity"),
                    "remaining_quantity": record.get("remaining_quantity"),
                    "reason": record.get("reason"),
                },
                update_index=expected_index, receipt_id=record.get("receipt_id"),
            )
        current = latest.get(order_id)
        if current is not None and record.get("update_index", 0) <= current.get("update_index", -1):
            if record != current:
                raise PaperOrderLifecycleError("order update sequence did not increase")
            continue
        latest[order_id] = record
    return [dict(row, id=order_id, state=row["status"], filled=row["filled_quantity"],
                 requested=row["requested_quantity"], remaining=row["remaining_quantity"])
            for order_id, row in sorted(latest.items())]
