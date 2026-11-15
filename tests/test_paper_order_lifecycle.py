from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.cockpit import CockpitError, blotter, blotter_from_journal
from financial_brain.forward_edge.paper_order_lifecycle import (
    PaperOrderLifecycleError,
    project_order_updates,
    updates_for_receipt,
)


NOW = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)


def _receipt():
    return {
        "status": "CANCELED",
        "receipt_id": "paper-receipt-1",
        "simulated_execution_at_utc": (NOW + timedelta(seconds=3)).isoformat(),
        "fills": [{"fill_id": "fill-1", "quantity": 4, "price": "100.05"}],
        "order_updates": [
            {"update_id": "u1", "status": "SUBMITTED", "at_utc": NOW.isoformat(),
             "requested_quantity": 10, "filled_quantity": 0, "remaining_quantity": 10},
            {"update_id": "u2", "status": "PARTIAL", "at_utc": (NOW + timedelta(seconds=1)).isoformat(),
             "requested_quantity": 10, "filled_quantity": 4, "remaining_quantity": 6},
            {"update_id": "u3", "status": "CANCELED", "at_utc": (NOW + timedelta(seconds=3)).isoformat(),
             "requested_quantity": 10, "filled_quantity": 4, "remaining_quantity": 6,
             "reason": "SESSION_END"},
        ],
    }


def test_order_updates_preserve_partial_fill_cancel_and_exact_leaves():
    updates = updates_for_receipt(order_id="paper-order-1", decision={"quantity": 10}, receipt=_receipt())
    assert [row["status"] for row in updates] == ["SUBMITTED", "PARTIAL", "CANCELED"]
    assert [(row["filled_quantity"], row["remaining_quantity"]) for row in updates] == [(0, 10), (4, 6), (4, 6)]
    projected = project_order_updates(updates, session_id=None)
    assert projected[0]["state"] == "CANCELED"
    assert projected[0]["requested"] == 10
    assert projected[0]["filled"] == 4
    assert projected[0]["remaining"] == 6


def test_receipt_without_requested_quantity_keeps_remaining_unknown():
    receipt = {"status": "MISSED", "receipt_id": "missed-1",
               "simulated_execution_at_utc": NOW.isoformat(), "fills": []}
    row = updates_for_receipt(order_id="o1", decision={"side": "BUY"}, receipt=receipt)[0]
    assert row["filled_quantity"] == 0
    assert row["requested_quantity"] is None
    assert row["remaining_quantity"] is None
    html = blotter_from_journal([row])
    assert "UNKNOWN" in html and "MISSED" in html


def test_update_rejects_inconsistent_or_decreasing_fill_totals():
    receipt = _receipt()
    receipt["order_updates"][1]["remaining_quantity"] = 5
    with pytest.raises(PaperOrderLifecycleError, match="requested minus filled"):
        updates_for_receipt(order_id="o1", decision={"quantity": 10}, receipt=receipt)
    receipt = _receipt()
    receipt["order_updates"][2]["filled_quantity"] = 3
    receipt["order_updates"][2]["remaining_quantity"] = 7
    with pytest.raises(PaperOrderLifecycleError, match="cannot decrease"):
        updates_for_receipt(order_id="o1", decision={"quantity": 10}, receipt=receipt)


def test_blotter_shows_exact_quantities_and_refuses_bad_reconciliation():
    html = blotter([{"id": "o1", "state": "PARTIAL", "requested": 10,
                     "filled": 4, "remaining": 6}])
    assert "Requested" in html and "Remaining" in html and ">10<" in html and ">4<" in html and ">6<" in html
    with pytest.raises(CockpitError, match="reconcile"):
        blotter([{"id": "o1", "state": "PARTIAL", "requested": 10,
                  "filled": 4, "remaining": 5}])
