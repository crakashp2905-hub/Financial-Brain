from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.approvals import Approval
from financial_brain.forward_edge.governance import FamilyEvidence, family_action_scope_sha256
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.paper_account_bridge import (
    ForwardPaperSessionService,
    PaperAccountBridgeError,
)
from financial_brain.forward_edge.paper_execution_adapter import MarketableQuoteSimulator
from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner
from financial_brain.kernel.paper_account import FeeSchedule, PaperAccount
from financial_brain.kernel.oms import State


DECISION_AT = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)
SCHEDULE = FeeSchedule("paper-cost-v1", date(2026, 1, 1), Decimal("10"), Decimal("10"))
MODEL_EVIDENCE = {"fee_schedule_sha256": SCHEDULE.sha256}


def _config():
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
    return ForwardRunConfig(
        session_id="account-bridge-session", exchange_date="2026-11-12", cohort_id="bridge-cohort",
        strategy_sha256="a" * 64, risk_policy_sha256="b" * 64,
        constraints_sha256="c" * 64, ledger_id="account-ledger", benchmark_id="NIFTY50",
        instruments=("NSE:INFY",), capital_inr=str(capital), mode=Mode.LIVE_PAPER,
        family_evidence=family, family_owner_approval=approval,
    )


def _event(at, bid="99.95", ask="100", bid_qty=20, ask_qty=20):
    return NormalizedEvent(
        "QUOTE", "NSE:INFY", at - timedelta(milliseconds=2), at - timedelta(milliseconds=1), at,
        tuple(sorted({"bid_price": bid, "ask_price": ask,
                      "bid_qty": str(bid_qty), "ask_qty": str(ask_qty)}.items())),
    )


def _service(tmp_path):
    config = _config()
    runner_path = tmp_path / f"{config.session_id}.runner.jsonl"
    account_path = tmp_path / "account.jsonl"
    account = PaperAccount(account_path, account_id="paper-main", initial_cash=Decimal("10000"),
                           fee_schedule=SCHEDULE)
    service = ForwardPaperSessionService(ForwardSessionRunner(runner_path), account)
    return service, config, account, runner_path, account_path


def test_service_reconciles_runner_fill_costs_position_and_close_mark_idempotently(tmp_path):
    service, config, account, runner_path, account_path = _service(tmp_path)
    events = [_event(DECISION_AT + timedelta(seconds=1)),
              _event(DECISION_AT + timedelta(seconds=2), bid="102", ask="102.05")]
    simulator = MarketableQuoteSimulator({"NSE:INFY": Decimal("0.05")}, **MODEL_EVIDENCE)
    preflight_calls = []
    kwargs = dict(
        preflight_fn=lambda: preflight_calls.append(True) or {"approved": True, "checks": {"cash": True}},
        decision_fn=lambda _events: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}, "instrument": "NSE:INFY", "quantity": 2},
        risk_fn=lambda _decision: {"approved": True, "account_cash_reserve_inr": "500"},
        execution_fn=simulator, decision_at=DECISION_AT,
    )

    first = service.run(config, events, **kwargs)
    cash_after_first = account.cash
    second = service.run(config, events, **kwargs)

    assert first["execution"]["status"] == "FILLED"
    assert first["account_reconciliation"] == second["account_reconciliation"]
    assert preflight_calls == [True]
    assert account.cash == cash_after_first == Decimal("9799.80")
    assert account.position("NSE:INFY").quantity == 2
    assert account.fees == Decimal("0.20")
    durable_fill = next(row for row in account.journal.records() if row.get("t") == "FILL")
    assert durable_fill["execution_evidence"]["fee_schedule_sha256"] == SCHEDULE.sha256
    assert durable_fill["execution_evidence"]["execution_model_sha256"] == first["execution"]["execution_model_sha256"]
    assert first["account_reconciliation"]["data_snapshot_sha256"] == first["execution"]["data_snapshot_sha256"]
    event_rows = [row for row in DurableJournal(runner_path).records()
                  if row.get("t") == "CANONICAL_TRADING_EVENT"]
    payloads = [json.loads(row["payload"]) for row in event_rows]
    assert [payload["event_type"] for payload in payloads] == [
        "IntentEvent", "OrderEvent", "OrderEvent", "FillEvent", "MarkEvent"
    ]
    assert len({row["event_sha256"] for row in event_rows}) == len(event_rows)
    assert len(event_rows) == len(first["account_reconciliation"]["canonical_event_sha256s"])
    assert account.ledger.balance("cash:paper-main", "INR") == account.cash
    assert account.ledger.trial_balance_ok()
    assert first["account_reconciliation"]["mark"]["marking_policy"] == "LONG_LIQUIDATION_BID"
    assert first["account_reconciliation"]["unrealized_pnl_inr"] == "4.00"
    assert PaperAccount(account_path, account_id="paper-main", initial_cash=Decimal("10000"),
                        fee_schedule=SCHEDULE).position("NSE:INFY").quantity == 2


def test_crash_after_durable_fill_and_cost_post_restarts_without_duplicate_economics(tmp_path, monkeypatch):
    service, config, account, runner_path, account_path = _service(tmp_path)
    events = [_event(DECISION_AT + timedelta(seconds=1)),
              _event(DECISION_AT + timedelta(seconds=2), bid="102", ask="102.05")]
    simulator = MarketableQuoteSimulator({"NSE:INFY": Decimal("0.05")}, **MODEL_EVIDENCE)
    kwargs = dict(
        preflight_fn=lambda: {"approved": True, "checks": {"cash": True}},
        decision_fn=lambda _events: {"side": "BUY", "expected_net_alpha": 0.02,
                                     "forecast_horizon_sessions": 1, "forecast_costs_included": True,
                                     "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033,
                                         "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001},
                                     "instrument": "NSE:INFY", "quantity": 2},
        risk_fn=lambda _decision: {"approved": True, "account_cash_reserve_inr": "500"},
        execution_fn=simulator, decision_at=DECISION_AT,
    )
    reference_dir = tmp_path / "reference"
    reference_dir.mkdir()
    reference_service, reference_config, reference_account, reference_runner_path, _ = _service(reference_dir)
    reference = reference_service.run(reference_config, events, **kwargs)
    append = DurableJournal.append

    def crash_after_account_fill(journal, record):
        sequence = append(journal, record)
        if journal.path == account_path and record.get("t") == "FILL":
            raise RuntimeError("simulated crash after durable fill and fee")
        return sequence

    monkeypatch.setattr(DurableJournal, "append", crash_after_account_fill)
    with pytest.raises(RuntimeError, match="after durable fill and fee"):
        service.run(config, events, **kwargs)
    monkeypatch.setattr(DurableJournal, "append", append)

    restarted_account = PaperAccount(account_path, account_id="paper-main", initial_cash=Decimal("10000"),
                                     fee_schedule=SCHEDULE)
    assert restarted_account.cash == Decimal("9799.80")
    assert restarted_account.fees == Decimal("0.20")
    assert restarted_account.position("NSE:INFY").quantity == 2
    restarted_service = ForwardPaperSessionService(ForwardSessionRunner(runner_path), restarted_account)
    recovered = restarted_service.run(config, events, **kwargs)

    assert recovered["status"] == "PAPER_COMPLETE"
    assert recovered["account_reconciliation"]["fees_inr"] == "0.20"
    assert recovered["account_reconciliation"] == reference["account_reconciliation"]
    assert DurableJournal(account_path).head == DurableJournal(reference_account.path).head
    records = restarted_account.journal.records()
    assert len([row for row in records if row.get("t") == "FILL"]) == 1
    assert len([row for row in records if row.get("t") == "INTENT"]) == 1
    recovered_runner_rows = DurableJournal(runner_path).records()
    reference_runner_rows = DurableJournal(reference_runner_path).records()
    recovered_receipt = next(row for row in recovered_runner_rows if row.get("t") == "PAPER_EXECUTION")
    reference_receipt = next(row for row in reference_runner_rows if row.get("t") == "PAPER_EXECUTION")
    assert recovered_receipt["execution"] == reference_receipt["execution"]
    recovered_receipt_hash = next(row["execution_sha256"] for row in recovered_runner_rows
                                  if row.get("t") == "OUTBOX_ACK")
    reference_receipt_hash = next(row["execution_sha256"] for row in reference_runner_rows
                                  if row.get("t") == "OUTBOX_ACK")
    assert recovered_receipt_hash == reference_receipt_hash
    assert len([row for row in recovered_runner_rows if row.get("t") == "ACCOUNT_RECONCILIATION"]) == 1


def test_partial_receipt_releases_unfilled_reservation_after_cancel(tmp_path):
    service, config, account, _, _ = _service(tmp_path)
    simulator = MarketableQuoteSimulator({"NSE:INFY": Decimal("0.05")}, **MODEL_EVIDENCE)

    result = service.run(
        config, [_event(DECISION_AT + timedelta(seconds=1), ask_qty=1)],
        preflight_fn=lambda: {"approved": True},
        decision_fn=lambda _events: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}, "instrument": "NSE:INFY", "quantity": 3},
        risk_fn=lambda _decision: {"approved": True, "account_cash_reserve_inr": "500"},
        execution_fn=simulator, decision_at=DECISION_AT,
    )

    assert result["execution"]["status"] == "PARTIAL"
    assert account.position("NSE:INFY").quantity == 1
    assert account.oms.order(result["intent_id"]).state is State.CANCELED
    assert account.oms.order(result["intent_id"]).reserved == 0


def test_canceled_partial_receipt_reconciles_fills_and_releases_remaining_reservation(tmp_path):
    service, config, account, _, _ = _service(tmp_path)
    simulator = MarketableQuoteSimulator({"NSE:INFY": Decimal("0.05")}, **MODEL_EVIDENCE)

    def canceled_execution(*args):
        receipt = simulator(*args)
        assert receipt["status"] == "PARTIAL"
        receipt["status"] = "CANCELED"
        receipt["reason"] = "OPERATOR_CANCEL"
        return receipt

    result = service.run(
        config, [_event(DECISION_AT + timedelta(seconds=1), ask_qty=1)],
        preflight_fn=lambda: {"approved": True},
        decision_fn=lambda _events: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}, "instrument": "NSE:INFY", "quantity": 3},
        risk_fn=lambda _decision: {"approved": True, "account_cash_reserve_inr": "500"},
        execution_fn=canceled_execution, decision_at=DECISION_AT,
    )

    assert result["execution"]["status"] == "CANCELED"
    assert account.position("NSE:INFY").quantity == 1
    assert account.oms.order(result["intent_id"]).state is State.CANCELED
    assert account.oms.order(result["intent_id"]).remaining == 2
    assert account.oms.order(result["intent_id"]).reserved == 0


def test_quote_quality_no_trade_reconciles_as_canceled_without_cash_or_position_changes(tmp_path):
    service, config, account, _, _ = _service(tmp_path)
    locked = _event(DECISION_AT + timedelta(seconds=1), bid="100", ask="100")

    result = service.run(
        config, [locked],
        preflight_fn=lambda: {"approved": True},
        decision_fn=lambda _events: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}, "instrument": "NSE:INFY", "quantity": 2},
        risk_fn=lambda _decision: {"approved": True, "account_cash_reserve_inr": "500"},
        execution_fn=MarketableQuoteSimulator({"NSE:INFY": Decimal("0.05")}, **MODEL_EVIDENCE),
        decision_at=DECISION_AT,
    )

    assert result["execution"]["status"] == "NO_TRADE"
    assert result["execution"]["quote_quality_rejections"][0]["reason_codes"] == ["LOCKED_BOOK"]
    assert result["account_reconciliation"]["order_state"] == "CANCELED"
    assert account.cash == Decimal("10000")
    assert account.position("NSE:INFY").quantity == 0


def test_buy_sync_requires_explicit_risk_cash_reserve(tmp_path):
    service, config, account, _, _ = _service(tmp_path)

    with pytest.raises(PaperAccountBridgeError, match="account_cash_reserve_inr"):
        service.run(
            config, [_event(DECISION_AT + timedelta(seconds=1))],
            preflight_fn=lambda: {"approved": True},
            decision_fn=lambda _events: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}, "instrument": "NSE:INFY", "quantity": 2},
            risk_fn=lambda _decision: {"approved": True},
            execution_fn=MarketableQuoteSimulator({"NSE:INFY": Decimal("0.05")}, **MODEL_EVIDENCE),
            decision_at=DECISION_AT,
        )

    assert account.cash == Decimal("10000")
    assert account.position("NSE:INFY").quantity == 0


def test_execution_fee_hash_mismatch_is_rejected_before_account_posting(tmp_path):
    service, config, account, _, _ = _service(tmp_path)
    simulator = MarketableQuoteSimulator({"NSE:INFY": Decimal("0.05")}, **MODEL_EVIDENCE)

    def wrong_fee_execution(decision, intent_id, events):
        receipt = simulator(decision, intent_id, events)
        receipt["fee_schedule_sha256"] = "f" * 64
        for fill in receipt["fills"]:
            fill["fee_schedule_sha256"] = "f" * 64
        return receipt

    with pytest.raises(PaperAccountBridgeError, match="fee hash"):
        service.run(
            config, [_event(DECISION_AT + timedelta(seconds=1))],
            preflight_fn=lambda: {"approved": True},
            decision_fn=lambda _events: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}, "instrument": "NSE:INFY", "quantity": 2},
            risk_fn=lambda _decision: {"approved": True, "account_cash_reserve_inr": "500"},
            execution_fn=wrong_fee_execution, decision_at=DECISION_AT,
        )

    assert account.cash == Decimal("10000")
    assert account.position("NSE:INFY").quantity == 0


def test_preflight_exception_is_durable_and_never_executes(tmp_path):
    service, config, account, runner_path, _ = _service(tmp_path)
    calls = []

    result = service.run(
        config, [_event(DECISION_AT + timedelta(seconds=1))],
        preflight_fn=lambda: (_ for _ in ()).throw(RuntimeError("private preflight details")),
        decision_fn=lambda _events: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}, "instrument": "NSE:INFY", "quantity": 2},
        risk_fn=lambda _decision: {"approved": True, "account_cash_reserve_inr": "500"},
        execution_fn=lambda *_: calls.append(True) or {}, decision_at=DECISION_AT,
    )

    assert result["status"] == "REJECTED"
    assert calls == []
    assert account.cash == Decimal("10000")
    assert "private preflight details" not in runner_path.read_text(encoding="utf-8")
