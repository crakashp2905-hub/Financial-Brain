"""Crash-safe Four-Node stream recovery on the repository's hash-chained journal."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from financial_brain.forward_edge.durable import DurableJournal, JournalCorrupt
from financial_brain.strategies.four_node.nodes import Node
from financial_brain.strategies.four_node.stream import FourNodeStateMachine, StreamEvent, StreamResult


class DurableFourNodeStreamError(RuntimeError):
    """Persisted Four-Node events cannot be safely replayed or appended."""


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _event_payload(event: StreamEvent) -> dict[str, Any]:
    node = event.node
    return {
        "event_id": event.event_id,
        "kind": event.kind,
        "session_id": event.session_id,
        "bar_index": event.bar_index,
        "close": event.close,
        "observed_at": event.observed_at.isoformat() if event.observed_at is not None else None,
        "available_at": event.available_at.isoformat() if event.available_at is not None else None,
        "source_sha256": event.source_sha256,
        "node": None if node is None else {
            "side": node.side,
            "price": node.price,
            "pivot_at": node.pivot_at,
            "confirmed_at": node.confirmed_at,
            "observable_at": node.observable_at,
            "node_id": node.node_id,
            "parent_chain": list(node.parent_chain),
            "source_bar_ids": list(node.source_bar_ids),
            "source_sha256": node.source_sha256,
        },
    }


def _stream_event(payload: dict[str, Any]) -> StreamEvent:
    raw_node = payload.get("node")
    node = None if raw_node is None else Node(
        int(raw_node["side"]), float(raw_node["price"]), int(raw_node["pivot_at"]),
        int(raw_node["confirmed_at"]),
        int(raw_node["observable_at"]) if raw_node.get("observable_at") is not None else None,
        str(raw_node["node_id"]), tuple(raw_node.get("parent_chain", ())),
        tuple(raw_node.get("source_bar_ids", ())), raw_node.get("source_sha256"),
    )
    return StreamEvent(
        str(payload["event_id"]), payload["kind"], str(payload["session_id"]),
        int(payload["bar_index"]), node=node,
        close=float(payload["close"]) if payload.get("close") is not None else None,
        observed_at=_time(payload.get("observed_at")),
        available_at=_time(payload.get("available_at")),
        source_sha256=payload.get("source_sha256"),
    )


class DurableFourNodeStream:
    """Persist before publishing in-memory state; restore by deterministic event replay.

    The adapter has no broker or order interface. Journal append is fsync-backed; an interrupted
    final line is discarded by ``DurableJournal`` and every committed event is replayed exactly once.
    """

    RECORD_TYPE = "FOUR_NODE_STREAM_EVENT_V1"

    def __init__(self, journal: DurableJournal, *, max_age_bars: int = 120) -> None:
        if not isinstance(journal, DurableJournal):
            raise TypeError("repository DurableJournal is required")
        self.journal = journal
        self.machine = FourNodeStateMachine(max_age_bars=max_age_bars)
        self._event_hashes: dict[str, str] = {}
        self._results: dict[str, StreamResult] = {}
        self._restore()

    @staticmethod
    def _digest(payload: dict[str, Any]) -> str:
        from financial_brain.forward_edge.durable import DurableSessionJournal

        return DurableSessionJournal.canonical(payload)[1]

    def _restore(self) -> None:
        try:
            for record in self.journal.records():
                if record.get("record_type") != self.RECORD_TYPE:
                    continue
                payload = record.get("event")
                if not isinstance(payload, dict):
                    raise DurableFourNodeStreamError("durable stream record has no event payload")
                event = _stream_event(payload)
                digest = self._digest(payload)
                prior = self._event_hashes.get(event.event_id)
                if prior is not None:
                    if prior != digest:
                        raise DurableFourNodeStreamError("durable event ID has conflicting payloads")
                    continue
                result = self.machine.apply(event)
                self._event_hashes[event.event_id] = digest
                self._results[event.event_id] = result
        except (JournalCorrupt, ValueError, KeyError, TypeError) as exc:
            raise DurableFourNodeStreamError(f"durable Four-Node replay failed closed: {exc}") from exc

    def apply(self, event: StreamEvent) -> StreamResult:
        payload = _event_payload(event)
        digest = self._digest(payload)
        previous = self._event_hashes.get(event.event_id)
        if previous is not None:
            if previous != digest:
                raise DurableFourNodeStreamError("event ID replayed with conflicting durable payload")
            return self._results[event.event_id]

        candidate_machine = deepcopy(self.machine)
        try:
            result = candidate_machine.apply(event)
        except (ValueError, TypeError) as exc:
            raise DurableFourNodeStreamError(f"Four-Node event refused before persistence: {exc}") from exc
        self.journal.append({"record_type": self.RECORD_TYPE, "event": payload})
        self.machine = candidate_machine
        self._event_hashes[event.event_id] = digest
        self._results[event.event_id] = result
        return result


__all__ = ["DurableFourNodeStream", "DurableFourNodeStreamError"]
