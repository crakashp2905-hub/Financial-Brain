from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.execution.oms.oco import arm_oco, resolve_oco_fill
from financial_brain.forward_edge.order_journal import OrderEvent, OrderJournal, OrderJournalError

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
EVIDENCE = "a" * 64
FILL = "f" * 64


def event(order_id, event_id, sequence, state, *, filled=0, evidence=None):
    return OrderEvent(order_id, event_id, sequence, state, NOW + timedelta(seconds=sequence), 10,
                      filled, "99.00" if filled else None, {"test": True},
                      fill_evidence_id=evidence)


def make_child(journal, order_id, *, filled=False):
    journal.append(event(order_id, f"{order_id}-0", 0, "CREATED"))
    journal.append(event(order_id, f"{order_id}-1", 1, "SUBMITTED"))
    if filled:
        journal.append(event(order_id, f"{order_id}-2", 2, "FILLED", filled=10, evidence=FILL))


def test_oco_cancel_request_keeps_sibling_live_until_broker_cancel_confirmation(tmp_path):
    journal = OrderJournal(tmp_path / "oco.sqlite")
    try:
        make_child(journal, "stop")
        make_child(journal, "target")
        armed = arm_oco(journal, group_id="position-1", stop_order_id="stop", target_order_id="target",
                        occurred_at=NOW, evidence_id=EVIDENCE)
        assert armed.event == "ARMED"
        journal.append(event("stop", "stop-fill", 2, "FILLED", filled=10, evidence=FILL))
        result = resolve_oco_fill(journal, group_id="position-1", triggered_order_id="stop",
                                  fill_event_id="stop-fill", occurred_at=NOW + timedelta(seconds=3),
                                  reconciliation_evidence_id=EVIDENCE)
        assert result.event == "CANCEL_REQUESTED"
        assert result.warning and "remains live" in result.warning
        assert journal.replay("oco:position-1")[-1]["state"] == "CANCEL_PENDING"
    finally:
        journal.close()


def test_oco_detects_late_sibling_fill_after_cancel_request(tmp_path):
    journal = OrderJournal(tmp_path / "oco.sqlite")
    try:
        make_child(journal, "stop")
        make_child(journal, "target")
        arm_oco(journal, group_id="position-1", stop_order_id="stop", target_order_id="target",
                occurred_at=NOW, evidence_id=EVIDENCE)
        journal.append(event("stop", "stop-fill", 2, "FILLED", filled=10, evidence=FILL))
        first = resolve_oco_fill(journal, group_id="position-1", triggered_order_id="stop",
                                 fill_event_id="stop-fill", occurred_at=NOW + timedelta(seconds=3),
                                 reconciliation_evidence_id=EVIDENCE)
        assert first.event == "CANCEL_REQUESTED"
        journal.append(event("target", "target-fill", 2, "FILLED", filled=10, evidence=FILL))
        raced = resolve_oco_fill(journal, group_id="position-1", triggered_order_id="stop",
                                 fill_event_id="stop-fill", occurred_at=NOW + timedelta(seconds=4),
                                 reconciliation_evidence_id=EVIDENCE)
        assert raced.event == "SIBLING_FILL_RACE"
        assert "reconcile exposure" in raced.warning
        assert journal.replay("oco:position-1")[-1]["state"] == "UNKNOWN"
    finally:
        journal.close()


def test_oco_replay_is_idempotent_and_conflicting_arm_is_rejected(tmp_path):
    journal = OrderJournal(tmp_path / "oco.sqlite")
    try:
        make_child(journal, "stop")
        make_child(journal, "target")
        kwargs = dict(group_id="position-1", stop_order_id="stop", target_order_id="target",
                      occurred_at=NOW, evidence_id=EVIDENCE)
        assert arm_oco(journal, **kwargs).event == "ARMED"
        assert arm_oco(journal, **kwargs).event == "REPLAY"
        with pytest.raises(OrderJournalError, match="distinct"):
            arm_oco(journal, **{**kwargs, "target_order_id": "stop"})
    finally:
        journal.close()


def test_oco_rejects_fill_without_exact_child_event(tmp_path):
    journal = OrderJournal(tmp_path / "oco.sqlite")
    try:
        make_child(journal, "stop")
        make_child(journal, "target")
        arm_oco(journal, group_id="position-1", stop_order_id="stop", target_order_id="target",
                occurred_at=NOW, evidence_id=EVIDENCE)
        with pytest.raises(OrderJournalError, match="exact durable fill event"):
            resolve_oco_fill(journal, group_id="position-1", triggered_order_id="stop",
                             fill_event_id="missing", occurred_at=NOW + timedelta(seconds=1),
                             reconciliation_evidence_id=EVIDENCE)
    finally:
        journal.close()

