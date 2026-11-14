from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner
from financial_brain.forward_edge.signal_seal import SignalSealError, seal_decision


NOW = datetime(2026, 11, 16, 4, 0, tzinfo=timezone.utc)


def _config(session_id="sealed-session"):
    return ForwardRunConfig(
        session_id=session_id, exchange_date="2026-11-16", cohort_id="sealed-cohort",
        strategy_sha256="a" * 64, risk_policy_sha256="b" * 64,
        constraints_sha256="c" * 64, ledger_id="sealed-ledger", benchmark_id="NIFTY50",
        instruments=("NSE:INFY",), capital_inr="100000", mode=Mode.LIVE_PAPER,
    )


def _event(offset_seconds: int):
    available = NOW + timedelta(seconds=offset_seconds)
    return NormalizedEvent("QUOTE", "NSE:INFY", available - timedelta(milliseconds=2),
                           available - timedelta(milliseconds=1), available,
                           (("bid_price", "99.95"), ("ask_price", "100")))


@pytest.mark.parametrize(("side", "risk_approved"), [("BUY", False), ("NONE", True), ("NO_TRADE", True)])
def test_runner_persists_sealed_action_or_abstention_before_risk_or_outcome(tmp_path, side, risk_approved):
    config = _config(f"sealed-{side.lower()}")
    path = tmp_path / f"{side}.jsonl"
    runner = ForwardSessionRunner(path)
    runner.record_preflight(config, {"approved": True, "status": "READY", "receipt": "preflight-1"})
    visible = _event(-2)
    future = _event(20)
    called = []
    result = runner.run(
        config, [visible, future], decision_at=NOW,
        decision_fn=lambda events: called.extend(events) or {
            "side": side, "instrument": "NSE:INFY", "reason": "NO_EDGE" if side != "BUY" else None,
        },
        risk_fn=lambda _decision: {"approved": risk_approved, "reason": "TEST_RISK_BLOCK"},
        execution_fn=lambda *_args: pytest.fail("blocked/abstaining decisions must not execute"),
    )
    rows = DurableJournal(path).records()
    seal = next(row for row in rows if row.get("t") == "DECISION")
    market_event = next(row for row in rows if row.get("t") == "MARKET_EVENT"
                        and row["event"]["available_utc"] == visible.available_utc.isoformat())
    assert seal["decision_at_utc"] == NOW.isoformat()
    assert seal["sealed_at_utc"] == NOW.isoformat()
    assert seal["timestamp_source"] == "SESSION_DECISION_CLOCK"
    assert seal["input_event_sha256s"] == [market_event["event_sha256"]]
    assert all(event.available_utc <= NOW for event in called)
    assert seal["input_manifest_sha256"] and seal["signal_sha256"]
    if side in {"NONE", "NO_TRADE"}:
        assert seal["decision"]["abstention_reason"] == "NO_EDGE"
    assert result["physical_orders"] is False


def test_seal_hashes_cover_config_strategy_signal_and_all_causal_inputs():
    event = _event(-1)
    cfg = _config()
    decision = {"side": "BUY", "instrument": "NSE:INFY", "quantity": 2}
    preflight = {"approved": True, "status": "READY"}
    seal = seal_decision(session_id=cfg.session_id, strategy_sha256=cfg.strategy_sha256,
                         config_sha256=cfg.sha256, decision_at=NOW, decision=decision,
                         inputs=[event], preflight=preflight, feed_health_sha256=None)
    expected_event = {"event_sha256": event.content_sha256(),
                      "available_at_utc": event.available_utc.isoformat(),
                      "instrument": event.instrument, "kind": event.kind}
    preflight_sha = hashlib.sha256(json.dumps(preflight, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()
    manifest = {"session_id": cfg.session_id, "strategy_sha256": cfg.strategy_sha256,
                "config_sha256": cfg.sha256, "decision_at_utc": NOW.isoformat(),
                "event_inputs": [expected_event], "preflight_sha256": preflight_sha,
                "feed_health_sha256": None}
    assert seal["input_manifest_sha256"] == hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    with pytest.raises(SignalSealError, match="not-yet-available"):
        seal_decision(session_id=cfg.session_id, strategy_sha256=cfg.strategy_sha256,
                      config_sha256=cfg.sha256, decision_at=NOW, decision=decision,
                      inputs=[_event(1)], preflight=preflight, feed_health_sha256=None)


def test_input_hash_changes_when_signal_or_source_changes():
    cfg = _config()
    a = seal_decision(session_id=cfg.session_id, strategy_sha256=cfg.strategy_sha256,
                      config_sha256=cfg.sha256, decision_at=NOW,
                      decision={"side": "NONE", "reason": "NO_EDGE"}, inputs=[_event(-1)],
                      preflight={"approved": True}, feed_health_sha256=None)
    b = seal_decision(session_id=cfg.session_id, strategy_sha256=cfg.strategy_sha256,
                      config_sha256=cfg.sha256, decision_at=NOW,
                      decision={"side": "NONE", "reason": "MISSING_FEED"}, inputs=[_event(-1)],
                      preflight={"approved": True}, feed_health_sha256=None)
    assert a["signal_sha256"] != b["signal_sha256"]
