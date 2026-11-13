from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.paper_execution_adapter import MarketableQuoteSimulator
from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner


DECISION_AT = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)
MODEL_EVIDENCE = {"fee_schedule_sha256": "a" * 64}


def _quote(available_at, *, bid="99.9", ask="100.1", bid_qty=20, ask_qty=3):
    return NormalizedEvent(
        "QUOTE", "NSE:INFY", available_at - timedelta(milliseconds=2),
        available_at - timedelta(milliseconds=1), available_at,
        tuple(sorted({"bid_price": bid, "ask_price": ask,
                      "bid_qty": str(bid_qty), "ask_qty": str(ask_qty)}.items())),
    )


def _decision(**overrides):
    return {"instrument": "NSE:INFY", "side": "BUY", "quantity": 5,
            "decision_at_utc": DECISION_AT.isoformat(), **overrides}


def test_quote_simulator_uses_first_quote_after_latency_and_caps_visible_size():
    early = _quote(DECISION_AT + timedelta(milliseconds=10), ask="90", ask_qty=50)
    executable = _quote(DECISION_AT + timedelta(milliseconds=25), ask="101.02", ask_qty=3)
    adapter = MarketableQuoteSimulator({"NSE:INFY": Decimal("0.05")},
                                       latency=timedelta(milliseconds=20), **MODEL_EVIDENCE)

    receipt = adapter(_decision(), "intent-1", [early, executable])

    assert receipt["status"] == "PARTIAL"
    assert receipt["fidelity"] == "SIMULATED_QUOTE"
    assert receipt["execution_model_id"] == "quote-v1"
    assert len(receipt["execution_model_sha256"]) == 64
    assert receipt["fills"][0]["latency_model_sha256"] == receipt["latency_model_sha256"]
    assert receipt["fills"][0]["data_snapshot_sha256"] == receipt["data_snapshot_sha256"]
    assert receipt["simulated_execution_at_utc"] == executable.available_utc.isoformat()
    assert receipt["fills"][0]["quantity"] == 3
    assert receipt["fills"][0]["price"] == "101.05"
    assert receipt["fills"][0]["quote_event_sha256"] == executable.content_sha256()


def test_quote_simulator_refuses_ltp_only_and_invalid_quotes():
    ltp_only = NormalizedEvent(
        "QUOTE", "NSE:INFY", DECISION_AT, DECISION_AT, DECISION_AT,
        (("last_price", "100"),),
    )
    crossed = _quote(DECISION_AT + timedelta(seconds=1), bid="102", ask="101")
    adapter = MarketableQuoteSimulator({"NSE:INFY": Decimal("0.05")}, **MODEL_EVIDENCE)

    receipt = adapter(_decision(), "intent-2", [ltp_only, crossed])

    assert receipt["status"] == "MISSED"
    assert receipt["fills"] == []
    assert "simulated_execution_at_utc" not in receipt


def test_quote_simulator_abstains_when_feed_health_fails():
    event = _quote(DECISION_AT + timedelta(seconds=1))
    adapter = MarketableQuoteSimulator(
        {"NSE:INFY": Decimal("0.05")},
        **MODEL_EVIDENCE,
        feed_health=lambda _events: (False, ["STALE_QUOTE"]),
    )

    receipt = adapter(_decision(), "intent-3", [event])

    assert receipt["status"] == "NO_TRADE"
    assert receipt["reason"] == "STALE_QUOTE"
    assert receipt["fills"] == []


def test_quote_simulator_rejects_fractional_decision_quantity():
    adapter = MarketableQuoteSimulator({"NSE:INFY": Decimal("0.05")}, **MODEL_EVIDENCE)

    with pytest.raises(ValueError, match="positive integer"):
        adapter(_decision(quantity=1.5), "intent-bad-quantity", [])


def test_quote_simulator_is_connected_to_durable_runner(tmp_path):
    config = ForwardRunConfig(
        session_id="quote-sim-session", exchange_date="2026-11-12", cohort_id="cohort-a",
        strategy_sha256="a" * 64, risk_policy_sha256="b" * 64,
        constraints_sha256="c" * 64, ledger_id="ledger-a", benchmark_id="NIFTY50",
        instruments=("NSE:INFY",), capital_inr="100000", mode=Mode.LIVE_PAPER,
    )
    runner = ForwardSessionRunner(tmp_path / "session.jsonl")
    runner.record_preflight(config, {"approved": True, "status": "PASS"})
    quote = _quote(DECISION_AT + timedelta(milliseconds=50))
    simulator = MarketableQuoteSimulator({"NSE:INFY": Decimal("0.05")}, **MODEL_EVIDENCE)

    result = runner.run(
        config, [quote], decision_at=DECISION_AT,
        decision_fn=lambda _events: {"instrument": "NSE:INFY", "side": "BUY", "quantity": 2},
        risk_fn=lambda _decision: {"approved": True}, execution_fn=simulator,
    )

    assert result["status"] == "PAPER_COMPLETE"
    assert result["execution"]["fidelity"] == "SIMULATED_QUOTE"
    assert result["execution"]["fills"][0]["quantity"] == 2
