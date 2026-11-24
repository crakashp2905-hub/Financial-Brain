from __future__ import annotations

import pytest

from financial_brain.strategies.four_node.nodes import Node
from financial_brain.strategies.four_node.stream import FourNodeStateMachine, StreamEvent


def _node(side: int, price: float, pivot: int, observed: int) -> Node:
    return Node(side, price, pivot, observed, source_bar_ids=(f"bar-{pivot}",))


def _sell_events() -> list[StreamEvent]:
    return [
        StreamEvent("n1", "NODE_CONFIRMED", "NSE:2026-11-24", 2, node=_node(1, 110, 1, 2)),
        StreamEvent("n2", "NODE_CONFIRMED", "NSE:2026-11-24", 4, node=_node(-1, 100, 3, 4)),
        StreamEvent("n3", "NODE_CONFIRMED", "NSE:2026-11-24", 6, node=_node(1, 108, 5, 6)),
        StreamEvent("bar-6", "BAR_CLOSED", "NSE:2026-11-24", 6, close=99),
        StreamEvent("bar-7", "BAR_CLOSED", "NSE:2026-11-24", 7, close=99),
    ]


def test_three_previously_observed_nodes_then_next_bar_breakout():
    machine, results = FourNodeStateMachine.replay(_sell_events())
    assert results[3].status == "NO_BREAKOUT"  # same close that reveals N3 is too early
    assert results[4].status == "BREAKOUT"
    candidate = results[4].candidates[0]
    assert candidate.side == "SELL"
    assert candidate.breakout_bar == 7
    assert candidate.actionable_after_bar == 8
    assert len(candidate.node_ids) == 3
    assert len(machine.nodes) == 3


def test_identical_event_replay_is_idempotent_and_conflict_is_rejected():
    events = _sell_events()
    machine = FourNodeStateMachine()
    for event in events[:3]:
        machine.apply(event)
    breakout = events[4]
    first = machine.apply(breakout)
    assert machine.apply(breakout) == first
    with pytest.raises(ValueError, match="conflicting payload"):
        machine.apply(StreamEvent(breakout.event_id, "BAR_CLOSED", breakout.session_id,
                                  breakout.bar_index, close=98))
    assert machine.apply(events[4]).candidates == first.candidates


def test_opposite_breakouts_on_one_bar_fail_closed():
    machine = FourNodeStateMachine()
    for i, (side, price) in enumerate(((1, 90), (-1, 100), (1, 80), (-1, 110))):
        machine.apply(StreamEvent(f"node-{i}", "NODE_CONFIRMED", "S", i + 1,
                                  node=_node(side, price, i, i + 1)))
    result = machine.apply(StreamEvent("conflict-bar", "BAR_CLOSED", "S", 6, close=90))
    assert result.status == "CONFLICT_NO_SIGNAL"
    assert result.candidates == ()
    assert result.reason_codes == ("OPPOSITE_BREAKOUTS_ON_SAME_BAR",)


def test_session_rollover_expiry_and_explicit_reset_clear_patterns():
    machine = FourNodeStateMachine(max_age_bars=2)
    node_event = StreamEvent("node", "NODE_CONFIRMED", "S1", 1,
                             node=_node(1, 10, 0, 1))
    machine.apply(node_event)
    reset = machine.apply(StreamEvent("reset", "SESSION_RESET", "S1", 2))
    assert reset.status == "RESET" and machine.nodes == []
    machine.apply(StreamEvent("node-2", "NODE_CONFIRMED", "S1", 1,
                              node=_node(1, 10, 0, 1)))
    expired = machine.apply(StreamEvent("expiry", "BAR_CLOSED", "S1", 3, close=9))
    assert expired.status == "EXPIRED" and machine.nodes == []
    machine.apply(StreamEvent("node-3", "NODE_CONFIRMED", "S1", 1,
                              node=_node(1, 10, 0, 1)))
    rollover = machine.apply(StreamEvent("next-session", "BAR_CLOSED", "S2", 2, close=9))
    assert "SESSION_ROLLOVER_RESET" in rollover.reason_codes
    assert machine.nodes == []


def test_online_and_batch_replay_results_are_identical():
    events = _sell_events()
    online = FourNodeStateMachine()
    online_results = [online.apply(event) for event in events]
    _, replay_results = FourNodeStateMachine.replay(events)
    assert online_results == replay_results
