"""FE-0072 append-only order transition journal and deterministic replay."""
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.order_journal import OrderEvent, OrderJournal, OrderJournalError

NOW = datetime(2026, 11, 15, 4, 0, tzinfo=timezone.utc)


def _event(seq: int, state: str, filled: int, *, event_id: str | None = None) -> OrderEvent:
    return OrderEvent("ord-1", event_id or f"evt-{seq}", seq, state, NOW + timedelta(seconds=seq),
                      10, filled, "100.00" if filled else None,
                      {"cash_delta": str(-100 * filled), "inventory_delta": filled})


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
