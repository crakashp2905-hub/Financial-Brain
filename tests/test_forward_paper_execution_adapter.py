from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.approvals import Approval
from financial_brain.forward_edge.governance import FamilyEvidence, family_action_scope_sha256
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
    return {"instrument": "NSE:INFY", "side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}, "quantity": 5,
            "decision_at_utc": DECISION_AT.isoformat(), **overrides}


def test_quote_simulator_uses_first_quote_after_latency_and_caps_visible_size():
    early = _quote(DECISION_AT + timedelta(milliseconds=10), ask="90", ask_qty=50)
    executable = _quote(DECISION_AT + timedelta(milliseconds=25), bid="100.9", ask="101.02", ask_qty=3)
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
    assert receipt["fills"][0]["execution_nature"] == "SIMULATED"
    assert receipt["fills"][0]["quote_observation"]["exchange"] == "NSE"
    assert receipt["fills"][0]["quote_observation"]["source"] == "UNDECLARED"
    assert receipt["fills"][0]["quote_observation"]["bid_price"] == "100.9"
    assert receipt["fills"][0]["quote_observation"]["ask_price"] == "101.02"
    assert receipt["fills"][0]["quote_observation"]["received_utc"] == executable.received_utc.isoformat()


def test_quote_simulator_refuses_ltp_only_and_invalid_quotes():
    ltp_only = NormalizedEvent(
        "QUOTE", "NSE:INFY", DECISION_AT, DECISION_AT, DECISION_AT,
        (("last_price", "100"),),
    )
    crossed = _quote(DECISION_AT + timedelta(seconds=1), bid="102", ask="101")
    adapter = MarketableQuoteSimulator({"NSE:INFY": Decimal("0.05")}, **MODEL_EVIDENCE)

    receipt = adapter(_decision(), "intent-2", [ltp_only, crossed])

    assert receipt["status"] == "NO_TRADE"
    assert receipt["reason"] == "QUOTE_QUALITY_BLOCK"
    assert receipt["fills"] == []
    assert receipt["simulated_execution_at_utc"] == DECISION_AT.isoformat()
    assert len(receipt["quote_quality_rejections"]) == 2


def test_quote_simulator_blocks_circuit_auction_and_freeze_violations():
    event = NormalizedEvent(
        "QUOTE", "NSE:INFY", DECISION_AT - timedelta(milliseconds=2),
        DECISION_AT - timedelta(milliseconds=1), DECISION_AT,
        tuple(sorted({"bid_price": "99.9", "ask_price": "100.1", "bid_qty": "20",
                      "ask_qty": "20", "circuit_hit": "true", "market_phase": "AUCTION",
                      "freeze_qty": "2"}.items())),
    )
    adapter = MarketableQuoteSimulator({"NSE:INFY": Decimal("0.05")}, **MODEL_EVIDENCE)

    receipt = adapter(_decision(quantity=5), "intent-quality", [event])

    assert receipt["status"] == "NO_TRADE"
    assert receipt["reason"] == "QUOTE_QUALITY_BLOCK"
    assert receipt["fills"] == []
    assert receipt["quote_quality_rejections"][0]["reason_codes"] == [
        "AUCTION_ACTIVE", "CIRCUIT_OR_HALT", "FREEZE_LIMIT"
    ]
    assert receipt["fidelity"] == "SIMULATED_QUOTE"


def test_execution_model_hash_freezes_configured_quote_quality_limits():
    event = _quote(DECISION_AT + timedelta(milliseconds=10), bid="100", ask="101")
    strict = MarketableQuoteSimulator({"NSE:INFY": Decimal("0.05")}, **MODEL_EVIDENCE)
    configured = MarketableQuoteSimulator({"NSE:INFY": Decimal("0.05")},
                                          max_spread_bps=150, **MODEL_EVIDENCE)

    strict_receipt = strict(_decision(quantity=2), "intent-strict", [event])
    configured_receipt = configured(_decision(quantity=2), "intent-configured", [event])

    assert strict_receipt["status"] == "NO_TRADE"
    assert configured_receipt["status"] == "FILLED"
    assert strict_receipt["execution_model_sha256"] != configured_receipt["execution_model_sha256"]


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
    capital = Decimal("100000")
    family = FamilyEvidence(
        family_id="test-family", status="VERIFIED", strategy_sha256="a" * 64,
        frozen_protocol_sha256="b" * 64, evidence_manifest_sha256="c" * 64,
        protocol_frozen=True, protocol_frozen_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        cohort_started_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
        verified_sessions=80, receipt_sessions=80, candidate_count=1,
        multiplicity_adjusted=True, cost_stress_passed=True,
        matched_control_lcb_bps=Decimal("1"), net_alpha_lcb_bps=Decimal("1"),
    )
    scope = family_action_scope_sha256(
        evidence=family, action="AUTO_EXECUTION_ENABLE", current_capital_inr=capital,
        requested_capital_inr=capital, execution_mode="PAPER")
    approval = Approval("STRATEGY_ENROLLMENT", family.family_id, scope, "owner:test", "human",
                        datetime(2025, 1, 1, tzinfo=timezone.utc), datetime(2035, 1, 1, tzinfo=timezone.utc))
    config = ForwardRunConfig(
        session_id="quote-sim-session", exchange_date="2026-11-12", cohort_id="cohort-a",
        strategy_sha256="a" * 64, risk_policy_sha256="b" * 64,
        constraints_sha256="c" * 64, ledger_id="ledger-a", benchmark_id="NIFTY50",
        instruments=("NSE:INFY",), capital_inr=str(capital), mode=Mode.LIVE_PAPER,
        family_evidence=family, family_owner_approval=approval,
    )
    runner = ForwardSessionRunner(tmp_path / "session.jsonl")
    runner.record_preflight(config, {"approved": True, "status": "PASS"})
    baseline = _quote(DECISION_AT - timedelta(milliseconds=10), bid="99.8", ask="100.0")
    quote = _quote(DECISION_AT + timedelta(milliseconds=50), bid="100.0", ask="100.2")
    simulator = MarketableQuoteSimulator({"NSE:INFY": Decimal("0.05")}, **MODEL_EVIDENCE)

    result = runner.run(
        config, [baseline, quote], decision_at=DECISION_AT,
        decision_fn=lambda _events: {"instrument": "NSE:INFY", "side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}, "quantity": 2},
        risk_fn=lambda _decision: {"approved": True}, execution_fn=simulator,
    )

    assert result["status"] == "PAPER_COMPLETE"
    assert result["execution"]["fidelity"] == "SIMULATED_QUOTE"
    assert result["execution"]["fills"][0]["quantity"] == 2
    diagnostic = result["execution"]["quote_arrival_shortfall"]
    assert diagnostic["benchmark_type"] == "SUBSEQUENT_EXECUTABLE_QUOTE"
    assert diagnostic["broker_fill_observation"] is False
    assert Decimal(diagnostic["shortfall_bp"]) == Decimal("20")
