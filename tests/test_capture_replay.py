from datetime import datetime, timedelta, timezone
from pathlib import Path

from financial_brain.forward_edge.capture_replay import verify_capture_replay_equivalence
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.session_runner import ForwardRunConfig


DECISION = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)


def _config():
    return ForwardRunConfig(
        session_id="PARITY-1", exchange_date="2026-11-12", cohort_id="PARITY-COHORT",
        strategy_sha256="a" * 64, risk_policy_sha256="b" * 64, constraints_sha256="c" * 64,
        ledger_id="PARITY-LEDGER", benchmark_id="NIFTY50", instruments=("NSE:INFY",),
        capital_inr="100000", mode=Mode.SHADOW,
    )


def _event(price="1500"):
    return NormalizedEvent(
        "QUOTE", "NSE:INFY", DECISION - timedelta(seconds=2), DECISION - timedelta(seconds=1),
        DECISION - timedelta(milliseconds=500), (("last_price", price),),
    )


def _execution(_decision, intent_id, _events):
    return {"status": "MISSED", "receipt_id": f"sim-{intent_id}", "fidelity": "SIMULATED_QUOTE",
            "simulated_execution_at_utc": (DECISION + timedelta(milliseconds=10)).isoformat(), "fills": []}


def test_capture_and_archive_replay_produce_identical_signal_and_intent_hashes(tmp_path: Path):
    event = _event()
    report = verify_capture_replay_equivalence(
        config=_config(), captured_events=[event], replayed_events=[event], decision_at=DECISION,
        decision_fn=lambda visible: {"side": "BUY", "instrument": "NSE:INFY", "quantity": len(visible)},
        risk_fn=lambda _: {"approved": True}, execution_fn=_execution,
        audit_path=tmp_path / "parity.jsonl",
    )
    again = verify_capture_replay_equivalence(
        config=_config(), captured_events=[event], replayed_events=[event], decision_at=DECISION,
        decision_fn=lambda visible: {"side": "BUY", "instrument": "NSE:INFY", "quantity": len(visible)},
        risk_fn=lambda _: {"approved": True}, execution_fn=_execution,
        audit_path=tmp_path / "parity.jsonl",
    )

    assert report["input_events_identical"] is True
    assert report["equivalent"] is True
    assert report["captured_signal_sha256"] == report["replayed_signal_sha256"]
    assert report["captured_order_intent_sha256"] == report["replayed_order_intent_sha256"]
    assert again == report
    assert len(DurableJournal(tmp_path / "parity.jsonl").records()) == 1


def test_capture_replay_mismatch_is_durably_reported_without_claiming_equivalence(tmp_path: Path):
    report = verify_capture_replay_equivalence(
        config=_config(), captured_events=[_event()], replayed_events=[_event("1501")],
        decision_at=DECISION,
        decision_fn=lambda visible: {"side": "BUY", "instrument": "NSE:INFY", "quantity": len(visible)},
        risk_fn=lambda _: {"approved": True}, execution_fn=_execution,
        audit_path=tmp_path / "mismatch.jsonl",
    )
    assert report["input_events_identical"] is False
    assert report["equivalent"] is False
    assert report["captured_signal_sha256"] is None
    assert DurableJournal(tmp_path / "mismatch.jsonl").records()[0]["t"] == "CAPTURE_REPLAY_PARITY"
