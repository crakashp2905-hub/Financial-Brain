"""Deterministic OMS and gross double-entry projection from durable paper receipts.

The projection is rebuilt from immutable forward intents and execution receipts. It is
gross of statutory charges and therefore cannot be treated as net P&L or promotion evidence.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from financial_brain.kernel.ledger import CREDIT, DEBIT, DoubleEntryLedger, Entry, Transaction
from financial_brain.kernel.oms import Oms, OmsError, State
from financial_brain.kernel.units import Money


class PaperAccountingError(ValueError):
    """Durable paper receipts do not form a consistent OMS and gross ledger history."""


def rebuild_paper_accounting(*, account_id: str, capital: Decimal,
                             events: list[dict[str, Any]], intents: list[dict[str, Any]],
                             receipts: list[dict[str, Any]]) -> dict[str, Any]:
    """Replay the durable receipt set into a paper OMS and balanced gross ledger."""
    if not account_id.strip() or not capital.is_finite() or capital <= 0:
        raise PaperAccountingError("paper account ID and positive finite capital are required")
    event_by_id = {event.get("accounting_event_id", event["event_id"]): event for event in events}
    intent_by_id: dict[str, dict[str, Any]] = {
        intent.get("accounting_order_id", intent["order_id"]): intent for intent in intents}
    receipt_by_id: dict[str, dict[str, Any]] = {}
    for receipt in receipts:
        order_id = str(receipt.get("accounting_order_id", receipt.get("order_id", "")))
        if not order_id or order_id in receipt_by_id:
            raise PaperAccountingError("execution receipt identities must be unique and non-empty")
        receipt_by_id[order_id] = receipt

    ledger = DoubleEntryLedger()
    ledger.post(Transaction("opening-capital", (
        Entry("cash", DEBIT, Money.of(capital, "INR")),
        Entry("owner_equity", CREDIT, Money.of(capital, "INR")),
    )))
    oms = Oms({account_id: capital})
    inventory: dict[str, int] = {}
    average_cost: dict[str, Decimal] = {}
    realized_gross = Decimal(0)
    order_states: dict[str, str] = {}
    completed_order_ids: list[str] = []
    charged_orders: list[dict[str, Any]] = []
    unresolved = sorted(set(intent_by_id) - set(receipt_by_id))
    errors: list[str] = []
    orphan_receipts = sorted(set(receipt_by_id) - set(intent_by_id))
    errors.extend(f"{order_id}: execution receipt has no durable intent" for order_id in orphan_receipts)

    receipt_order = {str(receipt.get("accounting_order_id", receipt["order_id"])): index
                     for index, receipt in enumerate(receipts)}
    ordered_intents = sorted(intents, key=lambda item: (
        receipt_order.get(str(item.get("accounting_order_id", item["order_id"])), len(receipt_order)),
        str(item.get("simulated_at", "")), str(item["order_id"])))
    for intent in ordered_intents:
        order_id = str(intent.get("accounting_order_id", intent["order_id"]))
        if order_id not in receipt_by_id:
            continue
        receipt = receipt_by_id[order_id]
        source_order_id = str(intent["order_id"])
        event = event_by_id.get(str(intent.get("accounting_event_id", intent["event_id"])))
        if event is None:
            errors.append(f"{order_id}: source market event is missing")
            continue
        instrument = str(event.get("instrument_id", ""))
        side = str(intent.get("side", ""))
        quantity = int(intent.get("quantity", 0))
        filled = int(receipt.get("filled_quantity", 0))
        status = str(receipt.get("status", ""))
        try:
            limit_price = Decimal(str(intent["limit_price"]))
            price = Decimal(str(receipt["average_price"])) if filled else None
        except Exception as exc:  # noqa: BLE001 - invalid durable financial record must be surfaced
            errors.append(f"{order_id}: invalid persisted price: {exc}")
            continue
        if not instrument or side not in {"BUY", "SELL"} or quantity <= 0 or not 0 <= filled <= quantity:
            errors.append(f"{order_id}: invalid persisted instrument, side or quantity")
            continue
        if status not in {"FILLED", "PARTIAL", "MISSED", "REJECTED"}:
            errors.append(f"{order_id}: unsupported execution status {status}")
            continue
        if (status in {"MISSED", "REJECTED"}) != (filled == 0):
            errors.append(f"{order_id}: receipt status conflicts with filled quantity")
            continue
        if status in {"FILLED", "PARTIAL"} and (price is None or not price.is_finite() or price <= 0):
            errors.append(f"{order_id}: filled receipt lacks a positive average price")
            continue
        cost_items = receipt.get("cost_items")
        if filled and cost_items is not None:
            try:
                charge_total = Decimal(str(receipt["charges_inr"]))
                item_total = sum(Decimal(str(item["total"])) for item in cost_items)
                if not charge_total.is_finite() or charge_total < 0 or item_total != charge_total:
                    raise ValueError("charge total differs from immutable line items")
                if any(item.get("order_id") != source_order_id for item in cost_items):
                    raise ValueError("cost line item order identity mismatch")
                charged_orders.append({"order_id": order_id, "charges_inr": format(charge_total, "f"),
                                       "cost_items": cost_items})
            except Exception as exc:  # noqa: BLE001 - corrupt persisted cost evidence must be visible
                errors.append(f"{order_id}: invalid cost evidence: {exc}")
                continue
        elif filled and receipt.get("charges_inr") is not None:
            errors.append(f"{order_id}: charges are present without itemized schedule evidence")
            continue
        if side == "SELL" and filled > inventory.get(instrument, 0):
            errors.append(f"{order_id}: sell fill exceeds paper inventory")
            continue
        reserve = limit_price * quantity if side == "BUY" and status != "REJECTED" else Decimal(0)
        try:
            oms.submit_intent(client_id=order_id, account=account_id, side=side, quantity=quantity,
                              limit=limit_price, reserve=reserve)
            oms.move(order_id, State.SUBMITTED)
            if status == "REJECTED":
                oms.move(order_id, State.REJECTED)
                order_states[order_id] = State.REJECTED.value
                completed_order_ids.append(order_id)
                continue
            oms.move(order_id, State.ACCEPTED)
            if filled:
                assert price is not None
                oms.record_fill(order_id, filled, price, provider_fill_id=order_id + ":sim-fill")
                notional = price * filled
                if side == "BUY":
                    old_qty = inventory.get(instrument, 0)
                    new_qty = old_qty + filled
                    old_cost = average_cost.get(instrument, Decimal(0))
                    average_cost[instrument] = (old_cost * old_qty + notional) / new_qty
                    inventory[instrument] = new_qty
                    entries: tuple[Entry, ...] = (
                        Entry("position:" + instrument, DEBIT, Money.of(notional, "INR")),
                        Entry("cash", CREDIT, Money.of(notional, "INR")))
                else:
                    old_qty = inventory[instrument]
                    cost_basis = average_cost[instrument] * filled
                    inventory[instrument] = old_qty - filled
                    if inventory[instrument] == 0:
                        inventory.pop(instrument)
                        average_cost.pop(instrument)
                    gross = notional - cost_basis
                    realized_gross += gross
                    entries_list = [Entry("cash", DEBIT, Money.of(notional, "INR")),
                                    Entry("position:" + instrument, CREDIT, Money.of(cost_basis, "INR"))]
                    if gross > 0:
                        entries_list.append(Entry("realized_gross_pnl", CREDIT, Money.of(gross, "INR")))
                    elif gross < 0:
                        entries_list.append(Entry("realized_gross_pnl", DEBIT, Money.of(-gross, "INR")))
                    entries = tuple(entries_list)
                ledger.post(Transaction(order_id + ":trade", entries))
            if cost_items is not None and filled:
                charge_total = Decimal(str(receipt["charges_inr"]))
                if charge_total:
                    oms.charge(account_id, charge_total)
                    ledger.post(Transaction(order_id + ":charges", (
                        Entry("transaction_costs", DEBIT, Money.of(charge_total, "INR")),
                        Entry("cash", CREDIT, Money.of(charge_total, "INR")),
                    )))
            if oms.order(order_id).remaining:
                oms.move(order_id, State.CANCEL_PENDING)
                oms.move(order_id, State.CANCELED)
            order_states[order_id] = oms.order(order_id).state.value
            completed_order_ids.append(order_id)
        except (OmsError, PaperAccountingError) as exc:
            errors.append(f"{order_id}: OMS replay rejected persisted receipt: {exc}")

    cash_oms = oms.cash(account_id)
    cash_ledger = ledger.balance("cash", "INR")
    if cash_oms != cash_ledger:
        errors.append(f"cash mismatch: OMS={cash_oms}, ledger={cash_ledger}")
    balanced = ledger.trial_balance_ok()
    if not balanced:
        errors.append("double-entry ledger is unbalanced")
    positions = {symbol: quantity for symbol, quantity in sorted(inventory.items())}
    charges = sum((Decimal(item["charges_inr"]) for item in charged_orders), Decimal(0))
    charged_fill_orders = {item["order_id"] for item in charged_orders}
    all_fills_costed = all(
        not int(receipt.get("filled_quantity", 0)) or
        str(receipt.get("accounting_order_id", receipt.get("order_id"))) in charged_fill_orders
        for receipt in receipts
    )
    has_filled_orders = any(int(receipt.get("filled_quantity", 0)) > 0 for receipt in receipts)
    net_eligible = has_filled_orders and not unresolved and not errors and all_fills_costed
    fidelity_components = sorted({str(receipt.get("simulation_fidelity") or "UNCLASSIFIED")
                                  for receipt in receipts})
    simulation_fidelity = (fidelity_components[0] if len(fidelity_components) == 1
                           else "MIXED" if fidelity_components else "UNCLASSIFIED")
    return {"schema": "fb.forward-paper-accounting.v1", "account_id": account_id,
            "capital_inr": format(capital, "f"), "cash_inr": format(cash_oms, "f"),
            "ledger_cash_inr": format(cash_ledger, "f"), "positions": positions,
            "average_cost_inr": {symbol: format(value, "f") for symbol, value in sorted(average_cost.items())},
            "realized_gross_pnl_inr": format(realized_gross, "f"),
            "charges_inr": format(charges, "f"),
            "realized_net_pnl_inr": format(realized_gross - charges, "f") if net_eligible else None,
            "filled_order_count": sum(int(receipt.get("filled_quantity", 0)) > 0 for receipt in receipts),
            "costed_fill_orders": sorted(charged_fill_orders), "cost_line_items": charged_orders,
            "oms_order_states": dict(sorted(order_states.items())),
            "completed_order_ids": completed_order_ids, "unresolved_order_ids": unresolved,
            "ledger_sha256": ledger.sha256(), "ledger_balanced": balanced,
            "cash_reconciled": cash_oms == cash_ledger, "errors": errors,
            "status": "INCOMPLETE" if unresolved or errors else
                     "RECONCILED_NET" if net_eligible else "RECONCILED_GROSS",
            "charges_assessed": net_eligible, "net_pnl_eligible": net_eligible,
            "simulation_fidelity": simulation_fidelity,
            "fidelity_components": fidelity_components,
            "accounting_policy": "AVERAGE_COST_NET_OF_VERSIONED_COST_SCHEDULE" if net_eligible else
                                 "AVERAGE_COST_GROSS_OF_FEES_TAXES_AND_FINANCING"}


__all__ = ["PaperAccountingError", "rebuild_paper_accounting"]
