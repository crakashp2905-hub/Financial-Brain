from __future__ import annotations

import pytest

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.strategies.four_node.durable_stream import (
    DurableFourNodeStream,
    DurableFourNodeStreamError,
    _event_payload,
)
from financial_brain.strategies.four_node.nodes import Node
from financial_brain.strategies.four_node.stream import StreamEvent


def _events() -> list[StreamEvent]:
    session = "NSE:2026-11-24"
    return [
        StreamEvent("n1", "NODE_CONFIRMED", session, 2, node=Node(1, 110, 1, 2)),
        StreamEvent("n2", "NODE_CONFIRMED", session, 4, node=Node(-1, 100, 3, 4)),
        StreamEvent("n3", "NODE_CONFIRMED", session, 6, node=Node(1, 108, 5, 6)),
        StreamEvent("bar-6", "BAR_CLOSED", session, 6, close=99),
        StreamEvent("bar-7", "BAR_CLOSED", session, 7, close=99),
    ]


def test_durable_stream_restores_state_and_retries_without_reapplying(tmp_path):
    path = tmp_path / "four-node.jsonl"
    stream = DurableFourNodeStream(DurableJournal(path))
    results = [stream.apply(event) for event in _events()]
    assert results[-1].status == "BREAKOUT"
    assert len(results[-1].candidates) == 1
    sequence = stream.journal.seq

    restored = DurableFourNodeStream(DurableJournal(path))
    assert restored.machine.nodes == stream.machine.nodes
    assert restored.machine.emitted_signal_ids == stream.machine.emitted_signal_ids
    assert restored.journal.seq == sequence
    # An old duplicate remains idempotent even though later timestamps were already replayed.
    assert restored.apply(_events()[-1]) == results[-1]
    assert restored.journal.seq == sequence
    assert not hasattr(results[-1], "order_id")


def test_durable_stream_rejects_conflicting_event_id_payload(tmp_path):
    stream = DurableFourNodeStream(DurableJournal(tmp_path / "four-node.jsonl"))
    event = _events()[0]
    stream.apply(event)
    with pytest.raises(DurableFourNodeStreamError, match="conflicting"):
        stream.apply(StreamEvent(event.event_id, "NODE_CONFIRMED", event.session_id,
                                 event.bar_index, node=Node(1, 111, 1, 2)))
    assert stream.journal.seq == 1


def test_torn_final_event_is_discarded_and_can_be_retried(tmp_path):
    path = tmp_path / "four-node.jsonl"
    events = _events()
    stream = DurableFourNodeStream(DurableJournal(path))
    for event in events[:-1]:
        stream.apply(event)
    stream.journal.append_torn({
        "record_type": DurableFourNodeStream.RECORD_TYPE,
        "event": _event_payload(events[-1]),
    })

    recovered_journal = DurableJournal(path)
    assert recovered_journal.torn_tail_discarded
    recovered = DurableFourNodeStream(recovered_journal)
    result = recovered.apply(events[-1])
    assert result.status == "BREAKOUT"
    assert recovered.journal.seq == len(events)

