"""FE-0072 append-only order transition journal and deterministic replay."""
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.order_journal import OrderEvent, OrderJournal, OrderJournalError

NOW = datetime(2026, 11, 15, 4, 0, tzinfo=timezone.utc)


def _event(seq: int, state: str, filled: int, *, event_id: str | None = None) -> OrderEvent:
    return OrderEvent("ord-1", event_id or f"evt-{seq}", seq, state, NOW + timedelta(seconds=seq),
                      10, filled, "100.00" if filled else None,
                      {"cash_delta": str(-100 * filled), "inventory_delta": filled},
                      fill_evidence_id="f" * 64 if filled else None)


def test_order_lifecycle_is_append_only_and_replay_hash_is_deterministic(tmp_path) -> None:
    journal = OrderJournal(tmp_path / "orders.sqlite3")
    try:
        for event in (_event(0, "CREATED", 0), _event(1, "SUBMITTED", 0),
                      _event(2, "PARTIAL", 4), _event(3, "FILLED", 10)):
            assert journal.append(event)
        assert not journal.append(_event(3, "FILLED", 10))
        replay = journal.replay("ord-1")
        assert [x["state"] for x in replay] == ["CREATED", "SUBMITTED", "PARTIAL", "FILLED"]
        assert journal.replay_hash("ord-1") == journal.replay_hash("ord-1")
    finally:
        journal.close()


def test_order_journal_rejects_gaps_illegal_transitions_decreasing_fills_and_id_conflicts(tmp_path) -> None:
    journal = OrderJournal(tmp_path / "orders.sqlite3")
    try:
        journal.append(_event(0, "CREATED", 0))
        with pytest.raises(OrderJournalError, match="monotonic"):
            journal.append(_event(2, "FILLED", 10))
        journal.append(_event(1, "SUBMITTED", 0))
        journal.append(_event(2, "PARTIAL", 5))
        with pytest.raises(OrderJournalError, match="cannot decrease"):
            journal.append(_event(3, "PARTIAL", 4))
        with pytest.raises(OrderJournalError, match="different content"):
            journal.append(_event(1, "SUBMITTED", 0, event_id="evt-0"))
    finally:
        journal.close()


def test_order_journal_restores_identical_replay_after_restart(tmp_path) -> None:
    path = tmp_path / "orders.sqlite3"
    journal = OrderJournal(path)
    journal.append(_event(0, "CREATED", 0))
    journal.append(_event(1, "SUBMITTED", 0))
    expected = journal.replay_hash("ord-1")
    journal.close()
    restarted = OrderJournal(path)
    try:
        assert restarted.replay_hash("ord-1") == expected
    finally:
        restarted.close()


def test_accepted_working_cancel_race_and_missed_order_states_are_distinct(tmp_path) -> None:
    journal = OrderJournal(tmp_path / "orders.sqlite3")
    try:
        events = (
            _event(0, "CREATED", 0), _event(1, "SUBMITTED", 0),
            _event(2, "ACCEPTED", 0), _event(3, "WORKING", 0),
            _event(4, "CANCEL_PENDING", 0), _event(5, "PARTIAL", 3),
            _event(6, "CANCELED", 3),
        )
        for event in events:
            assert journal.append(event)
        assert [row["state"] for row in journal.replay("ord-1")] == [
            "CREATED", "SUBMITTED", "ACCEPTED", "WORKING", "CANCEL_PENDING", "PARTIAL", "CANCELED"
        ]
        assert journal.replay("ord-1")[-1]["filled_quantity"] == 3

        missed = OrderEvent("missed", "missed-0", 0, "CREATED", NOW, 4, 0, None, {})
        journal.append(missed)
        journal.append(OrderEvent("missed", "missed-1", 1, "SUBMITTED", NOW, 4, 0, None, {}))
        journal.append(OrderEvent("missed", "missed-2", 2, "MISSED", NOW, 4, 0, None,
                                  {"reason": "NO_EXECUTABLE_DEPTH"}))
        assert journal.replay("missed")[-1]["state"] == "MISSED"
    finally:
        journal.close()


def test_unknown_and_timeout_require_evidence_before_resolution_and_never_invent_fill(tmp_path) -> None:
    journal = OrderJournal(tmp_path / "orders.sqlite3")
    try:
        journal.append(_event(0, "CREATED", 0))
        journal.append(_event(1, "SUBMITTED", 0))
        journal.append(_event(2, "TIMED_OUT", 0))
        journal.append(_event(3, "UNKNOWN", 0))
        with pytest.raises(OrderJournalError, match="reconciliation evidence"):
            journal.append(_event(4, "FILLED", 10))
        reconciled = OrderEvent("ord-1", "evt-4-reconciled", 4, "MISSED", NOW + timedelta(seconds=4),
                                10, 0, None, {"reason": "broker found no order"},
                                reconciliation_evidence_id="a" * 64)
        journal.append(reconciled)
        assert journal.replay("ord-1")[-1]["filled_quantity"] == 0
    finally:
        journal.close()


def test_order_state_audit_is_point_in_time_and_reports_ambiguous_orders(tmp_path) -> None:
    journal = OrderJournal(tmp_path / "orders.sqlite3")
    try:
        journal.append(_event(0, "CREATED", 0))
        journal.append(_event(1, "SUBMITTED", 0))
        journal.append(_event(2, "UNKNOWN", 0))
        audit_at_unknown = journal.audit_state(NOW + timedelta(seconds=2))
        assert audit_at_unknown["states"] == {"ord-1": "UNKNOWN"}
        assert audit_at_unknown["ambiguous_order_ids"] == ["ord-1"]
        assert len(audit_at_unknown["sha256"]) == 64

        journal.append(OrderEvent("ord-1", "evt-3-reconciled", 3, "MISSED", NOW + timedelta(seconds=3),
                                  10, 0, None, {"reason": "broker absence"},
                                  reconciliation_evidence_id="b" * 64))
        audit_after_resolution = journal.audit_state(NOW + timedelta(seconds=4))
        assert audit_after_resolution["states"] == {"ord-1": "MISSED"}
        assert audit_after_resolution["ambiguous_order_ids"] == []
        assert audit_after_resolution["sha256"] != audit_at_unknown["sha256"]
    finally:
        journal.close()


def test_fill_increase_without_immutable_receipt_is_rejected(tmp_path) -> None:
    journal = OrderJournal(tmp_path / "orders.sqlite3")
    try:
        journal.append(_event(0, "CREATED", 0))
        journal.append(_event(1, "SUBMITTED", 0))
        unsupported = OrderEvent("ord-1", "no-fill-evidence", 2, "FILLED", NOW + timedelta(seconds=2),
                                 10, 10, "100.00", {"inventory_delta": 10})
        with pytest.raises(OrderJournalError, match="immutable fill evidence"):
            journal.append(unsupported)
    finally:
        journal.close()


def test_new_order_cannot_start_with_a_synthesized_fill() -> None:
    with pytest.raises(OrderJournalError, match="cannot carry a confirmed fill"):
        _event(0, "CREATED", 1)
