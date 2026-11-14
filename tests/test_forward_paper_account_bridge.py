from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.approvals import Approval
from financial_brain.forward_edge.charter import EvaluationIdentity, ExperimentCharter
from financial_brain.forward_edge.governance import FamilyEvidence, family_action_scope_sha256
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.paper_account_bridge import (
    ForwardPaperSessionService,
    PaperAccountBridgeError,
)
from financial_brain.forward_edge.paper_execution_adapter import MarketableQuoteSimulator
from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner
from financial_brain.forward_edge.registry import Benchmark, MechanismHypothesis, Registry, Status, StrategySpec
from financial_brain.kernel.paper_account import FeeSchedule, PaperAccount
from financial_brain.kernel.oms import State
from financial_brain.kernel.registered_paper_books import (
    FamilyCapitalAuthorization,
    FrozenPaperBookPlan,
    RegisteredPaperBooks,
    StrategyBookAllocation,
)


DECISION_AT = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)
SCHEDULE = FeeSchedule("paper-cost-v1", date(2026, 1, 1), Decimal("10"), Decimal("10"))
MODEL_EVIDENCE = {"fee_schedule_sha256": SCHEDULE.sha256}
STRATEGY_SHA = "a" * 64


def _registered_strategy():
    strategy_id = "test-momentum"
    benchmark = Benchmark("NIFTY50", "INDEX", "f" * 64, date(2026, 11, 1))
    identity = EvaluationIdentity(
        strategy_code_sha256=STRATEGY_SHA, model_code_sha256="1" * 64,
        data_snapshot_sha256="2" * 64, data_rights_id="paper-test",
        data_rights_sha256="3" * 64, market_calendar_id="NSE-test",
        market_calendar_sha256="4" * 64, universe_sha256="5" * 64,
        execution_cost_id="paper-cost-v1", execution_cost_sha256="6" * 64,
        risk_policy_sha256="7" * 64, benchmarks=(("NIFTY50", "f" * 64),),
    )
    charter = ExperimentCharter(
        charter_id="paper-test-v1", version=1, strategy_id=strategy_id,
        code_sha256=STRATEGY_SHA, capital_inr="10000", start_on="2026-11-12",
        end_on="2027-03-31", universe=("NSE_EQ",), benchmarks=("NIFTY50",),
        horizon_sessions=5, evaluation_identity=identity,
    )
    spec = StrategySpec(
        strategy_id=strategy_id, family="momentum", version=1, code_sha256=STRATEGY_SHA,
        asset_scope=("NSE_EQ",), decision_clock="minute_close", direction="LONG",
        exit_rule="time_5_sessions", horizon_sessions=5, data_requirements=("quote",),
        source_lineage="test:registered-paper-adapter", status=Status.SHADOW_ONLY,
        benchmark_id="NIFTY50", mechanism_hypothesis=MechanismHypothesis(
            "Delayed trend following can leave continuation.",
            "Slow portfolio rebalancing can extend the response.",
            "Late trend entrants pay spread and impact.", ("liquid trend",),
            "The advantage disappears after full execution costs.",
        ),
    )
    registry = Registry()
    registry.register(spec)
    registry.register_benchmark(benchmark)
    approval = Approval(
        "STRATEGY_ENROLLMENT", strategy_id, charter.sha256(), "owner:test", "human",
        datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2035, 1, 1, tzinfo=timezone.utc),
    )
    eligible = registry.transition(strategy_id, Status.PAPER_ELIGIBLE,
                                    reason="test-only scoped enrollment", approval=approval,
                                    charter=charter, now=DECISION_AT)
    return eligible, registry


def _config():
    capital = Decimal("10000")
    family = FamilyEvidence(
        family_id="test-momentum", status="VERIFIED", strategy_sha256="a" * 64,
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
        strategy_sha256=STRATEGY_SHA, risk_policy_sha256="b" * 64,
        constraints_sha256="c" * 64, ledger_id="account-ledger", benchmark_id="NIFTY50",
        instruments=("NSE:INFY",), capital_inr=str(capital), mode=Mode.LIVE_PAPER,
        family_evidence=family, family_owner_approval=approval,
    )


def _registered_paper_books(tmp_path, family):
    capital = Decimal("10000")
    scope = family_action_scope_sha256(
        evidence=family, action="INITIAL_ALLOCATION", current_capital_inr=Decimal(0),
        requested_capital_inr=capital, execution_mode="PAPER")
    approval = Approval(
        "STRATEGY_ENROLLMENT", family.family_id, scope, "owner:test", "human",
        datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2035, 1, 1, tzinfo=timezone.utc))
    authorization = FamilyCapitalAuthorization(
        strategy_id=family.family_id, action="INITIAL_ALLOCATION", current_capital_inr=Decimal(0),
        requested_capital_inr=capital, evidence=family, owner_approval=approval)
    plan = FrozenPaperBookPlan(
        plan_id="paper-plan-v1", registered_at=datetime(2026, 11, 11, tzinfo=timezone.utc),
        effective_from=datetime(2026, 11, 12, tzinfo=timezone.utc), total_capital_inr=capital,
        books=(StrategyBookAllocation(family.family_id, "paper-main", capital),),
        eligible_strategy_ids=(family.family_id,), eligibility_evidence_id="eligibility:test",
        registration_evidence_id="registration:test", family_capital_authorizations=(authorization,))
    books = RegisteredPaperBooks(tmp_path / "paper-books.jsonl")
    books.register(plan)
    return books


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
    books = _registered_paper_books(tmp_path, config.family_evidence)
    strategy_spec, registry = _registered_strategy()
    service = ForwardPaperSessionService(ForwardSessionRunner(runner_path), account,
                                         strategy_spec=strategy_spec, registry=registry,
                                         paper_books=books, paper_book_plan_id="paper-plan-v1")
    return service, config, account, runner_path, account_path


def test_session_evidence_and_paper_account_authority_journals_cannot_alias(tmp_path):
    service, config, account, _runner_path, account_path = _service(tmp_path)
    service.runner = ForwardSessionRunner(account_path)
    before = account.journal.records()

    with pytest.raises(PaperAccountBridgeError, match="must be distinct"):
        service._validate_strategy(config, DECISION_AT)

    assert account.journal.records() == before


def test_forward_paper_service_requires_account_opening_capital_to_match_frozen_plan(tmp_path):
    service, config, _account, runner_path, _account_path = _service(tmp_path)
    wrong_account = PaperAccount(tmp_path / "wrong-capital.jsonl", account_id="paper-main",
                                 initial_cash=Decimal("9999"), fee_schedule=SCHEDULE)
    before = wrong_account.journal.records()
    mismatched = ForwardPaperSessionService(
        ForwardSessionRunner(runner_path), wrong_account,
        strategy_spec=service.strategy_spec, registry=service.registry,
        paper_books=service.paper_books, paper_book_plan_id=service.paper_book_plan_id,
    )

    with pytest.raises(PaperAccountBridgeError, match="opening capital must match"):
        mismatched._validate_strategy(config, DECISION_AT)

    assert wrong_account.journal.records() == before


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


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda config: replace(config, strategy_sha256="d" * 64), "strategy digest"),
        (lambda config: replace(config, benchmark_id="BANKNIFTY"), "frozen strategy benchmark"),
        (lambda config: replace(config, mode=Mode.RESEARCH_REPLAY), "LIVE_PAPER sessions only"),
    ],
)
def test_paper_service_rejects_unbound_strategy_before_preflight_or_journal_write(
        tmp_path, mutate, message):
    service, config, account, runner_path, _ = _service(tmp_path)
    preflight_calls = []
    with pytest.raises(PaperAccountBridgeError, match=message):
        service.run(
            mutate(config), [_event(DECISION_AT + timedelta(seconds=1))],
            preflight_fn=lambda: preflight_calls.append(True) or {"approved": True},
            decision_fn=lambda _events: {"side": "NONE", "reason": "NO_SIGNAL"},
            risk_fn=lambda _decision: {"approved": True},
            execution_fn=lambda *_: {}, decision_at=DECISION_AT,
        )
    assert preflight_calls == []
    assert not runner_path.exists()
    assert account.cash == Decimal("10000")


def test_paper_service_rechecks_registry_status_before_each_run(tmp_path):
    service, config, account, runner_path, _ = _service(tmp_path)
    service.registry.transition(service.strategy_spec.strategy_id, Status.SHADOW_ONLY,
                                reason="eligibility revoked")
    preflight_calls = []
    with pytest.raises(PaperAccountBridgeError, match="exact registered paper-capable version"):
        service.run(
            config, [_event(DECISION_AT + timedelta(seconds=1))],
            preflight_fn=lambda: preflight_calls.append(True) or {"approved": True},
            decision_fn=lambda _events: {"side": "NONE", "reason": "NO_SIGNAL"},
            risk_fn=lambda _decision: {"approved": True},
            execution_fn=lambda *_: {}, decision_at=DECISION_AT,
        )
    assert preflight_calls == []
    assert not runner_path.exists()
    assert account.cash == Decimal("10000")


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
    restarted_service = ForwardPaperSessionService(
        ForwardSessionRunner(runner_path), restarted_account,
        strategy_spec=service.strategy_spec, registry=service.registry,
        paper_books=service.paper_books, paper_book_plan_id=service.paper_book_plan_id,
    )
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
