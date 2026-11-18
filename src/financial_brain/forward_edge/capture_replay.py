"""Offline parity check for normalized capture events and archive replay (PE-0044)."""
from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner


def _digest(value: object) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def verify_capture_replay_equivalence(*, config: ForwardRunConfig,
                                      captured_events: Iterable[NormalizedEvent],
                                      replayed_events: Iterable[NormalizedEvent],
                                      decision_at: datetime,
                                      decision_fn: Callable[[list[NormalizedEvent]], dict],
                                      risk_fn: Callable[[dict], dict],
                                      execution_fn: Callable[[dict, str, list[NormalizedEvent]], dict],
                                      audit_path: Path) -> dict:
    """Run both event sequences through the same offline runner and compare frozen hashes.

    No broker adapter is available in ForwardSessionRunner. The two runs are isolated
    in temporary journals; only the compact parity result is appended to `audit_path`.
    """
    captured = list(captured_events)
    replayed = list(replayed_events)
    captured_hashes = [event.content_sha256() for event in captured]
    replayed_hashes = [event.content_sha256() for event in replayed]
    report = {
        "session_id": config.session_id,
        "config_sha256": config.sha256,
        "captured_event_sha256s": captured_hashes,
        "replayed_event_sha256s": replayed_hashes,
        "input_events_identical": captured_hashes == replayed_hashes,
        "captured_signal_sha256": None,
        "replayed_signal_sha256": None,
        "captured_order_intent_sha256": None,
        "replayed_order_intent_sha256": None,
        "equivalent": False,
        "physical_orders": False,
    }
    if report["input_events_identical"]:
        with tempfile.TemporaryDirectory(prefix="financial-brain-capture-replay-") as tmp:
            results = []
            journals = []
            for name, event_set in (("captured", captured), ("replayed", replayed)):
                path = Path(tmp) / f"{name}.jsonl"
                runner = ForwardSessionRunner(path)
                runner.record_preflight(config, {"approved": True, "status": "OFFLINE_PARITY"})
                result = runner.run(config, event_set, decision_at=decision_at,
                                    decision_fn=decision_fn, risk_fn=risk_fn,
                                    execution_fn=execution_fn)
                results.append(result)
                journals.append(DurableJournal(path).records())
            for label, records in zip(("captured", "replayed"), journals):
                decision_row = next(row for row in records if row.get("t") == "DECISION")
                visible_hashes = [row["event_sha256"] for row in records
                                  if row.get("t") == "MARKET_EVENT"
                                  and row["event"]["available_utc"] <= decision_row["decision_at_utc"]]
                report[f"{label}_signal_sha256"] = _digest({
                    "decision": decision_row["decision"], "visible_event_sha256s": visible_hashes,
                })
                intent = next((row for row in records if row.get("t") == "PAPER_INTENT"), None)
                report[f"{label}_order_intent_sha256"] = _digest(intent) if intent is not None else None
            report["captured_status"] = results[0]["status"]
            report["replayed_status"] = results[1]["status"]
            report["equivalent"] = (
                report["captured_signal_sha256"] == report["replayed_signal_sha256"]
                and report["captured_order_intent_sha256"] == report["replayed_order_intent_sha256"]
            )
    report["audit_id"] = _digest(report)
    journal = DurableJournal(audit_path)
    prior = next((row for row in journal.records()
                  if row.get("t") == "CAPTURE_REPLAY_PARITY" and row.get("audit_id") == report["audit_id"]), None)
    if prior is None:
        journal.append({"t": "CAPTURE_REPLAY_PARITY", **report})
    elif {key: value for key, value in prior.items() if key != "t"} != report:
        raise ValueError("capture-replay audit ID collides with different parity evidence")
    return report
