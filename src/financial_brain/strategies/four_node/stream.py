"""Deterministic event-driven Four-Node pattern state machine (FB-2514 to FB-2517).

This module discovers paper/research breakout candidates only. It has no broker dependency and
never creates orders. A candidate becomes actionable on a strictly later bar/quote event.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from hashlib import sha256
from math import isfinite
from typing import Literal

from financial_brain.strategies.four_node.nodes import Node

EventKind = Literal["NODE_CONFIRMED", "BAR_CLOSED", "SESSION_RESET"]


@dataclass(frozen=True)
class StreamEvent:
    event_id: str
    kind: EventKind
    session_id: str
    bar_index: int
    node: Node | None = None
    close: float | None = None
    observed_at: datetime | None = None
    available_at: datetime | None = None
    source_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.session_id.strip():
            raise ValueError("event and session IDs are required")
        if self.kind not in {"NODE_CONFIRMED", "BAR_CLOSED", "SESSION_RESET"}:
            raise ValueError("unsupported event kind")
        if self.bar_index < 0:
            raise ValueError("bar_index must be nonnegative")
        for label, value in (("observation", self.observed_at), ("availability", self.available_at)):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"stream {label} time must be timezone-aware")
        if (self.observed_at is not None and self.available_at is not None
                and self.available_at < self.observed_at):
            raise ValueError("stream data cannot be available before its observation")
        if self.source_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise ValueError("stream source must be a lowercase SHA-256")
        if self.kind == "NODE_CONFIRMED" and self.node is None:
            raise ValueError("NODE_CONFIRMED requires a node")
        if self.kind == "BAR_CLOSED" and (self.close is None or not isfinite(self.close)):
            raise ValueError("BAR_CLOSED requires a finite close")
        if self.kind == "SESSION_RESET" and (self.node is not None or self.close is not None):
            raise ValueError("SESSION_RESET carries no market payload")


@dataclass(frozen=True)
class BreakoutCandidate:
    signal_id: str
    side: Literal["BUY", "SELL"]
    node_ids: tuple[str, str, str]
    breakout_bar: int
    actionable_after_bar: int
    breakout_at: datetime | None = None
    available_at: datetime | None = None
    source_sha256: str | None = None

    def __post_init__(self) -> None:
        if (not self.signal_id.strip() or self.side not in {"BUY", "SELL"}
                or len(self.node_ids) != 3 or any(not node_id.strip() for node_id in self.node_ids)):
            raise ValueError("breakout candidate requires a signal ID, side and three node IDs")
        if self.breakout_bar < 0 or self.actionable_after_bar <= self.breakout_bar:
            raise ValueError("breakout candidate must become actionable after its breakout bar")
        for label, value in (("breakout", self.breakout_at), ("availability", self.available_at)):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"candidate {label} time must be timezone-aware")
        if (self.breakout_at is not None and self.available_at is not None
                and self.available_at < self.breakout_at):
            raise ValueError("candidate cannot be available before its breakout")
        if self.source_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise ValueError("candidate source must be a lowercase SHA-256")


@dataclass(frozen=True)
class StreamResult:
    event_id: str
    status: str
    reason_codes: tuple[str, ...]
    node_ids: tuple[str, ...]
    candidates: tuple[BreakoutCandidate, ...] = ()


def _fingerprint(event: StreamEvent) -> str:
    node = event.node
    material = (event.event_id, event.kind, event.session_id, event.bar_index, event.close,
                None if event.observed_at is None else event.observed_at.isoformat(),
                None if event.available_at is None else event.available_at.isoformat(), event.source_sha256,
                None if node is None else (node.node_id, node.side, node.price, node.pivot_at,
                                           node.confirmed_at, node.observable_at,
                                           node.parent_chain, node.source_bar_ids, node.source_sha256))
    return sha256(repr(material).encode("utf-8")).hexdigest()


def _direction(nodes: tuple[Node, Node, Node]) -> Literal["BUY", "SELL"] | None:
    sides = tuple(node.side for node in nodes)
    if sides == (1, -1, 1):
        return "SELL"
    if sides == (-1, 1, -1):
        return "BUY"
    return None


@dataclass
class FourNodeStateMachine:
    """Online/replay-identical N1..N4 state with expiry and duplicate-event protection."""

    max_age_bars: int = 120
    nodes: list[Node] = field(default_factory=list)
    session_id: str | None = None
    seen_events: dict[str, tuple[str, StreamResult]] = field(default_factory=dict)
    emitted_signal_ids: set[str] = field(default_factory=set)
    last_observed_at: datetime | None = None
    last_available_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.max_age_bars < 1:
            raise ValueError("max_age_bars must be positive")

    def apply(self, event: StreamEvent) -> StreamResult:
        fingerprint = _fingerprint(event)
        prior = self.seen_events.get(event.event_id)
        if prior is not None:
            if prior[0] != fingerprint:
                raise ValueError("event ID replayed with conflicting payload")
            return prior[1]
        event_time = event.available_at or event.observed_at
        if (event.observed_at is not None and self.last_observed_at is not None
                and event.observed_at < self.last_observed_at):
            raise ValueError("stream source observation time moved backwards")
        if (event_time is not None and self.last_available_at is not None
                and event_time < self.last_available_at):
            raise ValueError("stream availability time moved backwards")

        reasons: list[str] = []
        candidates: tuple[BreakoutCandidate, ...] = ()
        rolled_session = self.session_id is not None and self.session_id != event.session_id
        if rolled_session:
            self.nodes.clear()
            self.emitted_signal_ids.clear()
        self.session_id = event.session_id

        if event.kind == "SESSION_RESET":
            self.nodes.clear()
            self.emitted_signal_ids.clear()
            reasons.append("SESSION_RESET")
            status = "RESET"
        elif event.kind == "NODE_CONFIRMED":
            node = event.node
            assert node is not None
            if node.observable_at is None or node.observable_at > event.bar_index:
                status, reasons = "REJECTED", ["NODE_NOT_YET_OBSERVABLE"]
            elif self.nodes and node.node_id == self.nodes[-1].node_id:
                status, reasons = "DUPLICATE", ["NODE_ALREADY_PRESENT"]
            elif self.nodes and self.nodes[-1].side == node.side:
                last = self.nodes[-1]
                more_extreme = node.price > last.price if node.side == 1 else node.price < last.price
                if more_extreme:
                    self.nodes[-1] = node
                    status, reasons = "UPDATED", ["LATEST_SAME_SIDE_NODE_REPLACED"]
                else:
                    status, reasons = "IGNORED", ["SAME_SIDE_NODE_NOT_MORE_EXTREME"]
            else:
                self.nodes.append(node)
                self.nodes = self.nodes[-4:]
                status, reasons = "NODE_ADDED", ["CONFIRMED_NODE_RETAINED"]
        else:
            assert event.kind == "BAR_CLOSED" and event.close is not None
            if self.nodes and event.bar_index - self.nodes[0].pivot_at > self.max_age_bars:
                self.nodes.clear()
                self.emitted_signal_ids.clear()
                status, reasons = "EXPIRED", ["PATTERN_MAX_AGE_EXCEEDED"]
            else:
                eligible = [node for node in self.nodes if node.observable_at is not None
                            and node.observable_at < event.bar_index]
                triples = [tuple(eligible[i:i + 3]) for i in range(max(0, len(eligible) - 2))]
                matches: list[tuple[Literal["BUY", "SELL"], tuple[Node, Node, Node]]] = []
                for raw in triples:
                    triple = (raw[0], raw[1], raw[2])
                    side = _direction(triple)
                    if side is None:
                        continue
                    crossed = event.close < triple[1].price if side == "SELL" else event.close > triple[1].price
                    if crossed:
                        matches.append((side, triple))
                sides = {side for side, _ in matches}
                if len(sides) > 1:
                    status, reasons = "CONFLICT_NO_SIGNAL", ["OPPOSITE_BREAKOUTS_ON_SAME_BAR"]
                elif matches:
                    side = matches[-1][0]
                    selected = [triple for candidate_side, triple in matches if candidate_side == side]
                    made: list[BreakoutCandidate] = []
                    for triple in selected:
                        node_ids = (triple[0].node_id, triple[1].node_id, triple[2].node_id)
                        signal_id = sha256(
                            f"{event.session_id}:{side}:{node_ids}".encode()
                        ).hexdigest()
                        if signal_id not in self.emitted_signal_ids:
                            self.emitted_signal_ids.add(signal_id)
                            made.append(BreakoutCandidate(
                                signal_id, side, node_ids, event.bar_index, event.bar_index + 1,
                                event.observed_at, event.available_at,
                                sha256(repr(tuple(
                                    [node.source_sha256 for node in triple] + [event.source_sha256]
                                )).encode("utf-8")).hexdigest()
                                if all(node.source_sha256 is not None for node in triple)
                                and event.source_sha256 is not None else None,
                            ))
                    candidates = tuple(made)
                    status = "BREAKOUT" if candidates else "DUPLICATE_SIGNAL"
                    reasons = ["CLOSE_CONFIRMED_N2_BREAK", "ACTIONABLE_ON_LATER_BAR"]
                else:
                    status, reasons = "NO_BREAKOUT", ["NO_QUALIFIED_N1_N2_N3_BREAK"]

        if rolled_session:
            reasons.insert(0, "SESSION_ROLLOVER_RESET")
        result = StreamResult(event.event_id, status, tuple(reasons),
                              tuple(node.node_id for node in self.nodes), candidates)
        self.seen_events[event.event_id] = (fingerprint, result)
        if event_time is not None:
            self.last_available_at = event_time
        if event.observed_at is not None:
            self.last_observed_at = event.observed_at
        return result

    @classmethod
    def replay(cls, events: list[StreamEvent], *, max_age_bars: int = 120
               ) -> tuple[FourNodeStateMachine, list[StreamResult]]:
        machine = cls(max_age_bars=max_age_bars)
        return machine, [machine.apply(event) for event in events]
