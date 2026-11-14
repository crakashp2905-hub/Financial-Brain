from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.pretrade_gates import (
    PreTradePolicy,
    PreTradeSnapshot,
    evaluate_pretrade,
)
from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner


UTC = timezone.utc
START = datetime(2026, 11, 16, 3, 40, tzinfo=UTC)
DECISION = START + timedelta(minutes=1)
DIGESTS = {name: (hex(ord(name[0]))[2:] * 64)[:64] for name in
           ("adv", "exposure", "cost", "model")}


@pytest.fixture
def policy():
    return PreTradePolicy(
        strategy_sha256="a" * 64, strategy_version="frozen-v3",
        frozen_at=START - timedelta(days=3), session_starts_at=START,
        max_quote_age_s=5, max_spread_bps=15, max_adv_age_days=2,
        max_adv_participation=0.10, minimum_net_alpha_bps=5,
        max_abs_exposure_inr=Decimal("5000"), minimum_model_confidence=0.60,
    )


@pytest.fixture
def snapshot():
    quote = NormalizedEvent(
        "QUOTE", "NSE:INFY", DECISION - timedelta(seconds=2), DECISION - timedelta(seconds=1),
        DECISION, tuple(sorted({"bid_price": "99.90", "ask_price": "100.00",
                                "bid_qty": "100", "ask_qty": "100"}.items())),
    )
    return PreTradeSnapshot(
        session_id="pretrade-session", strategy_sha256="a" * 64,
        instrument="NSE:INFY", side="BUY", quantity=20,
        decision_at=DECISION + timedelta(seconds=1), quote=quote,
        adv_quantity=1000, adv_as_of=date(2026, 11, 16), adv_source_sha256=DIGESTS["adv"],
        current_signed_exposure_inr=Decimal("1000"), exposure_evidence_sha256=DIGESTS["exposure"],
        gross_alpha_bps=20.0, fees_bps=2.0, spread_cost_bps=2.0, impact_bps=1.0,
        cost_evidence_sha256=DIGESTS["cost"], model_confidence=0.8,
        model_evidence_sha256=DIGESTS["model"],
    )


def test_all_frozen_pretrade_gates_allow_only_supported_order(policy, snapshot):
    verdict = evaluate_pretrade(policy, snapshot)
    assert verdict.approved is True
    assert verdict.order_notional_inr == "2000.00"
    assert verdict.expected_net_alpha_bps == 15.0
    assert {check.name for check in verdict.checks} == {
        "frozen_policy", "intent", "spread_and_quote_health", "liquidity_and_capacity",
        "cost_to_net_alpha", "portfolio_exposure", "model_confidence",
    }
    assert verdict.as_risk_verdict()["approved"] is True


@pytest.mark.parametrize(
    ("changes", "gate"),
    [
        ({"model_confidence": 0.2}, "model_confidence"),
        ({"gross_alpha_bps": 5.0}, "cost_to_net_alpha"),
        ({"current_signed_exposure_inr": Decimal("4000")}, "portfolio_exposure"),
        ({"adv_quantity": 100}, "liquidity_and_capacity"),
        ({"adv_source_sha256": None}, "liquidity_and_capacity"),
        ({"cost_evidence_sha256": None}, "cost_to_net_alpha"),
    ],
)
def test_missing_or_over_limit_inputs_fail_their_specific_gate(policy, snapshot, changes, gate):
    verdict = evaluate_pretrade(policy, PreTradeSnapshot(**{**snapshot.__dict__, **changes}))
    result = next(check for check in verdict.checks if check.name == gate)
    assert verdict.approved is False
    assert result.passed is False
    assert result.reason


def test_excessive_spread_and_insufficient_displayed_size_fail_closed(policy, snapshot):
    tight_spread_policy = PreTradePolicy(**{
        **policy.__dict__, "max_spread_bps": 5,
    })
    assert not evaluate_pretrade(tight_spread_policy, snapshot).approved
    thin = NormalizedEvent(
        "QUOTE", "NSE:INFY", DECISION - timedelta(seconds=2), DECISION - timedelta(seconds=1),
        DECISION, tuple(sorted({"bid_price": "99.90", "ask_price": "100.00",
                                "bid_qty": "100", "ask_qty": "2"}.items())),
    )
    verdict = evaluate_pretrade(policy, PreTradeSnapshot(**{**snapshot.__dict__, "quote": thin}))
    capacity = next(check for check in verdict.checks if check.name == "liquidity_and_capacity")
    assert capacity.passed is False


def test_expired_adv_and_future_quote_do_not_pass(policy, snapshot):
    stale_adv = PreTradeSnapshot(**{**snapshot.__dict__, "adv_as_of": date(2026, 11, 1)})
    assert not evaluate_pretrade(policy, stale_adv).approved
    future_event = NormalizedEvent(
        "QUOTE", "NSE:INFY", snapshot.decision_at + timedelta(seconds=1),
        snapshot.decision_at + timedelta(seconds=2), snapshot.decision_at + timedelta(seconds=3),
        tuple(sorted({"bid_price": "99.90", "ask_price": "100.00",
                      "bid_qty": "100", "ask_qty": "100"}.items())),
    )
    future_quote = PreTradeSnapshot(**{**snapshot.__dict__, "quote": future_event})
    verdict = evaluate_pretrade(policy, future_quote)
    spread_gate = next(check for check in verdict.checks if check.name == "spread_and_quote_health")
    assert spread_gate.passed is False


def test_failed_gate_is_written_as_durable_attributable_paper_skip(tmp_path, policy, snapshot):
    config = ForwardRunConfig(
        session_id=snapshot.session_id, exchange_date="2026-11-16", cohort_id="pretrade-cohort",
        strategy_sha256=policy.strategy_sha256, risk_policy_sha256="b" * 64,
        constraints_sha256="c" * 64, ledger_id="pretrade-ledger", benchmark_id="NIFTY50",
        instruments=(snapshot.instrument,), capital_inr="100000", mode=Mode.LIVE_PAPER,
        authority_strategy_id="test-strategy", authority_account_id="test-account",
    )
    runner = ForwardSessionRunner(tmp_path / "paper.jsonl")
    runner.record_preflight(config, {"approved": True, "status": "READY"})
    failed_snapshot = PreTradeSnapshot(**{**snapshot.__dict__, "model_confidence": 0.2})
    gate = evaluate_pretrade(policy, failed_snapshot)
    result = runner.run(
        config, [snapshot.quote], decision_at=snapshot.decision_at,
        decision_fn=lambda _events: {"side": "BUY", "instrument": snapshot.instrument,
                                     "quantity": snapshot.quantity},
        risk_fn=lambda _decision: gate.as_risk_verdict(),
        execution_fn=lambda *_args: pytest.fail("a blocked pre-trade gate must not execute"),
    )
    records = DurableJournal(runner.journal_path).records()
    skips = [row for row in records if row.get("t") == "PAPER_SKIP"]
    assert result["status"] == "REJECTED"
    assert len(skips) == 1
    skip = skips[0]
    assert skip["candidate_intent_id"]
    assert skip["signal_sha256"] and skip["input_manifest_sha256"]
    assert "MODEL_CONFIDENCE_OR_EVIDENCE_FAILED" in skip["reason_codes"]
    assert skip["risk_verdict"]["verdict_sha256"] == gate.verdict_sha256
