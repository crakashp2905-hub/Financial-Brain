from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.market_data import FeedHealth, NormalizedEvent
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.cockpit import blotter_from_journal
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.approvals import Approval
from financial_brain.forward_edge.governance import FamilyEvidence, family_action_scope_sha256
from financial_brain.forward_edge.session_clock import SessionClockProvider
from financial_brain.forward_edge.session_runner import (
    ExecutionOutcomeUnknown,
    ForwardRunConfig,
    ForwardSessionRunner,
    RunnerError,
)
from financial_brain.forward_edge.degradation_monitor import (
    Action as DegradationAction,
    DegradationMonitor,
    FrozenThresholds,
    RollingObservation,
)
from financial_brain.forward_edge.uncertainty_sizing import (
    UncertaintySizingPolicy,
    UncertaintySizingRegistry,
)
from financial_brain.forward_edge.forecast_cost_benefit import (
    ForecastBenefitPolicyRegistry,
    calibrate_forecast_benefit_policy,
)
from financial_brain.kernel.paper_authority import PaperExecutionAuthorityRegistry


NOW = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)


def test_direct_live_runner_requires_ids_and_claims_authority_before_journal_write(tmp_path):
    journal_path = tmp_path / "session.jsonl"
    runner = ForwardSessionRunner(journal_path)
    config = _config()
    registry = PaperExecutionAuthorityRegistry(tmp_path / "paper_authority.sqlite3")
    registry.claim(strategy_id="test-strategy", exchange_date=config.exchange_date,
                   engine_id="legacy_intraday", account_id="legacy-book")

    with pytest.raises(RunnerError, match="authority conflict"):
        runner.run(config, [], decision_at=NOW,
                   decision_fn=lambda _events: {}, risk_fn=lambda _decision: {},
                   execution_fn=lambda *_: {})

    assert not journal_path.exists()


def test_direct_live_runner_rejects_missing_authority_identity_before_writes(tmp_path):
    journal_path = tmp_path / "session.jsonl"
    runner = ForwardSessionRunner(journal_path)
    config = _config(authority_strategy_id=None, authority_account_id=None)

    with pytest.raises(RunnerError, match="authority IDs"):
        runner.run(config, [], decision_at=NOW,
                   decision_fn=lambda _events: {}, risk_fn=lambda _decision: {},
                   execution_fn=lambda *_: {})

    assert not journal_path.exists()


def _config(**changes):
    values = dict(session_id="S-1", exchange_date="2026-11-12", cohort_id="C1",
                  strategy_sha256="a" * 64, risk_policy_sha256="b" * 64,
                  constraints_sha256="c" * 64, ledger_id="LEDGER-1",
                  benchmark_id="NIFTY50", instruments=("NSE:INFY",),
                  capital_inr="1000000", mode=Mode.LIVE_PAPER,
                  authority_strategy_id="test-strategy", authority_account_id="test-account")
    values.update(changes)
    if "family_evidence" not in changes and "family_owner_approval" not in changes:
        evidence = FamilyEvidence(
            family_id="test-family", status="VERIFIED", strategy_sha256="a" * 64,
            frozen_protocol_sha256="b" * 64, evidence_manifest_sha256="c" * 64,
            protocol_frozen=True, protocol_frozen_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            cohort_started_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            verified_sessions=80, receipt_sessions=80, candidate_count=1,
            multiplicity_adjusted=True, cost_stress_passed=True,
            matched_control_lcb_bps=Decimal("1"), net_alpha_lcb_bps=Decimal("1"),
        )
        capital = Decimal(values["capital_inr"])
        scope = family_action_scope_sha256(
            evidence=evidence, action="AUTO_EXECUTION_ENABLE",
            current_capital_inr=capital, requested_capital_inr=capital,
            execution_mode="PAPER",
        )
        approval = Approval(
            "STRATEGY_ENROLLMENT", evidence.family_id, scope, "owner:test", "human",
            datetime(2025, 1, 1, tzinfo=timezone.utc),
            datetime(2035, 1, 1, tzinfo=timezone.utc),
        )
        values["family_evidence"] = evidence
        values["family_owner_approval"] = approval
    return ForwardRunConfig(**values)


def _event(available=NOW - timedelta(seconds=1)):
    return NormalizedEvent("QUOTE", "NSE:INFY", available - timedelta(milliseconds=5),
                           available - timedelta(milliseconds=2), available,
                           (("last_price", "1500"),))


def _approve_preflight(runner, config=None):
    runner.record_preflight(
        config or _config(), {"approved": True, "status": "PASS", "checks": {"clock": True, "market_feed": True}}
    )


def _net_alpha_forecast(net=0.01, horizon=1, *, fees=0.001, spread=0.001, impact=0.001):
    benchmark = 0.01
    return {"horizon": horizon, "expected_return": benchmark + fees + spread + impact + net,
            "benchmark": benchmark, "fees": fees, "spread": spread, "impact": impact}


def _sizing_policy():
    return UncertaintySizingPolicy(
        strategy_sha256="a" * 64, cohort_id="C1", standardized_error_multiplier=1.0,
        confidence_z=1.0, development_data_sha256="b" * 64,
        calibration_protocol_sha256="c" * 64, development_start="2026-10-01",
        development_end="2026-10-31", validation_start="2026-11-12", observation_count=30,
        frozen_at_utc="2026-11-01T00:00:00+00:00",
    )


def _benefit_policy():
    observations = [{"split": "DEVELOPMENT", "exchange_date": "2026-10-01",
                     "forecast_benefit": 0.02, "realized_excess": 0.01,
                     "decision_sha256": f"{i:064x}", "forecast_model_sha256": "d" * 64}
                    for i in range(1, 31)]
    return calibrate_forecast_benefit_policy(
        strategy_sha256="a" * 64, cohort_id="C1", forecast_model_sha256="d" * 64,
        observations=observations, validation_start=date.fromisoformat("2026-11-12"),
        frozen_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        calibration_protocol_sha256="e" * 64,
    )


def test_runner_persists_visible_events_decision_risk_and_paper_execution(tmp_path):
    runner = ForwardSessionRunner(tmp_path / "session.jsonl")
    _approve_preflight(runner)
    calls = []
    result = runner.run(_config(), [_event(), _event(NOW + timedelta(seconds=1))], decision_at=NOW,
                        decision_fn=lambda events: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}, "count": len(events)},
                        risk_fn=lambda decision: {"approved": True},
                        execution_fn=lambda decision, intent, events: calls.append((intent, events)) or {
                            "status": "MISSED", "receipt_id": "sim-receipt-1", "fidelity": "SIMULATED_QUOTE"
                        })
    assert result["status"] == "PAPER_COMPLETE"
    assert result["event_count"] == 2
    assert result["decision_event_count"] == 1
    assert result["physical_orders"] is False
    assert len(calls) == 1 and len(calls[0][1]) == 2
    record_types = [r["t"] for r in DurableJournal(tmp_path / "session.jsonl").records()]
    assert record_types == ["SESSION_PLAN", "SESSION_STATE", "SESSION_STATE", "SESSION_STATE",
                            "PREFLIGHT_RESULT", "MARKET_EVENT", "MARKET_EVENT", "DECISION", "RISK_VERDICT", "SESSION_STATE",
                            "PAPER_INTENT", "OUTBOX_PENDING", "PAPER_EXECUTION", "OUTBOX_ACK", "PAPER_ORDER_UPDATE", "PAPER_SKIP",
                            "SESSION_STATE", "SESSION_CLOSED"]
    records = DurableJournal(tmp_path / "session.jsonl").records()
    family_gate = next(r["risk"]["family_automation_gate"] for r in records
                       if r["t"] == "RISK_VERDICT")
    assert family_gate["required"] is True and family_gate["approved"] is True
    states = [r["state"] for r in DurableJournal(tmp_path / "session.jsonl").records()
              if r["t"] == "SESSION_STATE"]
    assert states == ["PLANNED", "PREFLIGHT", "OPEN", "DRAINING", "CLOSED"]


def test_live_paper_order_is_blocked_without_family_evidence_and_owner_approval(tmp_path):
    runner = ForwardSessionRunner(tmp_path / "session.jsonl")
    config = _config(family_evidence=None, family_owner_approval=None)
    _approve_preflight(runner, config)
    execution_calls = []
    result = runner.run(
        config, [_event()], decision_at=NOW,
        decision_fn=lambda _: {"side": "BUY", "expected_net_alpha": 0.02,
                               "forecast_horizon_sessions": 1, "forecast_costs_included": True,
                               "net_alpha_forecast": _net_alpha_forecast()},
        risk_fn=lambda _: {"approved": True},
        execution_fn=lambda *args: execution_calls.append(args) or {},
    )
    assert result["status"] == "REJECTED"
    assert result["reason"] == "FAMILY_AUTOMATION_GATE_BLOCKED"
    assert execution_calls == []
    records = DurableJournal(tmp_path / "session.jsonl").records()
    assert not any(row["t"] == "PAPER_INTENT" for row in records)
    gate = next(row["risk"]["family_automation_gate"] for row in records
                if row["t"] == "RISK_VERDICT")
    assert gate["reasons"] == ["FAMILY_EVIDENCE_REQUIRED"]


def test_live_paper_order_is_blocked_when_family_owner_approval_is_expired(tmp_path):
    base = _config()
    assert base.family_evidence is not None and base.family_owner_approval is not None
    expired = Approval(
        base.family_owner_approval.kind, base.family_owner_approval.subject,
        base.family_owner_approval.scope_sha256, "owner:test", "human",
        datetime(2025, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    config = _config(family_evidence=base.family_evidence, family_owner_approval=expired)
    runner = ForwardSessionRunner(tmp_path / "expired-approval.jsonl")
    _approve_preflight(runner, config)
    calls = []
    result = runner.run(
        config, [_event()], decision_at=NOW,
        decision_fn=lambda _: {"side": "BUY", "expected_net_alpha": 0.02,
                               "forecast_horizon_sessions": 1, "forecast_costs_included": True,
                               "net_alpha_forecast": _net_alpha_forecast()},
        risk_fn=lambda _: {"approved": True},
        execution_fn=lambda *args: calls.append(args) or {},
    )
    assert result["reason"] == "FAMILY_AUTOMATION_GATE_BLOCKED"
    assert calls == []
    records = DurableJournal(tmp_path / "expired-approval.jsonl").records()
    gate = next(row["risk"]["family_automation_gate"] for row in records
                if row["t"] == "RISK_VERDICT")
    assert any("approval has expired" in reason for reason in gate["reasons"])


def test_order_updates_are_journaled_during_execution_and_render_exact_remaining_qty(tmp_path):
    path = tmp_path / "order-updates.jsonl"
    runner = ForwardSessionRunner(path)
    config = _config()
    _approve_preflight(runner, config)

    def execute(decision, intent_id, _events):
        runner.record_paper_order_update(config, intent_id, {
            "update_id": "submitted-1", "status": "SUBMITTED",
            "at_utc": (NOW + timedelta(seconds=1)).isoformat(),
            "requested_quantity": 10, "filled_quantity": 0,
            "remaining_quantity": 10,
        })
        runner.record_paper_order_update(config, intent_id, {
            "update_id": "partial-1", "status": "PARTIAL",
            "at_utc": (NOW + timedelta(seconds=2)).isoformat(),
            "requested_quantity": 10, "filled_quantity": 4,
            "remaining_quantity": 6,
        })
        return {
            "status": "CANCELED", "receipt_id": "receipt-cancel-1",
            "fidelity": "SIMULATED_QUOTE",
            "simulated_execution_at_utc": (NOW + timedelta(seconds=3)).isoformat(),
            "fills": [{"fill_id": "fill-cancel-1", "quantity": 4, "price": "100.05"}],
            "reason": "SESSION_END",
        }

    result = runner.run(
        config, [_event()], decision_at=NOW,
        decision_fn=lambda _: {
            "side": "BUY", "quantity": 10,
            "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1,
            "forecast_costs_included": True,
            "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033,
                                    "benchmark": 0.01, "fees": 0.001,
                                    "spread": 0.001, "impact": 0.001},
        },
        risk_fn=lambda _: {"approved": True}, execution_fn=execute,
    )
    assert result["execution"]["status"] == "CANCELED"
    records = DurableJournal(path).records()
    order_records = [row for row in records if row["t"] == "PAPER_ORDER_UPDATE"]
    assert [row["status"] for row in order_records] == ["SUBMITTED", "PARTIAL", "CANCELED"]
    assert order_records[-1]["requested_quantity"] == 10
    assert order_records[-1]["filled_quantity"] == 4
    assert order_records[-1]["remaining_quantity"] == 6
    html = blotter_from_journal(records, session_id=config.session_id)
    assert "Order update history" in html and "SESSION_END" in html
    assert ">10<" in html and ">4<" in html and ">6<" in html


def test_session_report_preserves_coverage_and_lost_opportunity_denominators(tmp_path):
    event = _event()
    digest = event.content_sha256()
    config = _config(instruments=("NSE:INFY", "NSE:TCS"))
    runner = ForwardSessionRunner(tmp_path / "coverage.jsonl")
    runner.record_preflight(config, {"approved": True, "status": "PASS"})
    result = runner.run(
        config, [event], decision_at=NOW,
        decision_fn=lambda _: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}, "instrument": "NSE:INFY", "quantity": 10,
                               "candidate_notional_inr": "10000"},
        risk_fn=lambda _: {"approved": True},
        execution_fn=lambda *_: {
            "status": "MISSED", "receipt_id": "missed-coverage",
            "fidelity": "SIMULATED_QUOTE", "simulated_execution_at_utc": NOW.isoformat(),
            "quote_inputs": [{"event_sha256": digest, "quote_id": f"event:{digest}"}],
            "fills": [],
        },
    )
    coverage = result["opportunity_coverage"]
    assert coverage["exchange_date"] == "2026-11-12"
    assert coverage["session_date_denominator"] == 1
    assert coverage["planned_instrument_count"] == 2
    assert coverage["quoted_instrument_count"] == 1
    assert coverage["missing_quote_instruments"] == ["NSE:TCS"]
    assert coverage["candidate_signal_count"] == 1
    assert coverage["candidate_signal_quote_covered_count"] == 1
    assert coverage["candidate_notional_denominator_inr"] == "10000"
    assert coverage["filled_notional_inr"] == "0"
    assert coverage["missed_candidate_signal_count"] == 1
    assert coverage["lost_opportunity_denominator_count"] == 1
    assert coverage["candidate_universe_scope"] == "STRATEGY_OUTPUT_NOT_MARKET_WIDE"
    assert coverage["missing_denominators"] == []


def test_session_report_exposes_unavailable_candidate_notional_denominator(tmp_path):
    runner = ForwardSessionRunner(tmp_path / "missing-denominator.jsonl")
    config = _config()
    runner.record_preflight(config, {"approved": True, "status": "PASS"})
    result = runner.run(
        config, [_event()], decision_at=NOW,
        decision_fn=lambda _: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}, "instrument": "NSE:INFY", "quantity": 2},
        risk_fn=lambda _: {"approved": True},
        execution_fn=lambda *_: {
            "status": "MISSED", "receipt_id": "missing-notional",
            "fidelity": "SIMULATED_QUOTE", "simulated_execution_at_utc": NOW.isoformat(),
            "fills": [],
        },
    )
    coverage = result["opportunity_coverage"]
    assert coverage["candidate_signal_count"] == 1
    assert coverage["candidate_notional_denominator_inr"] is None
    assert coverage["missing_denominators"] == ["CANDIDATE_NOTIONAL_INR", "EXECUTION_QUOTE_RECEIPTS"]


def test_runner_uses_injected_decision_and_simulated_execution_clocks(tmp_path):
    from financial_brain.forward_edge.session_clock import SessionClockProvider

    ticks = iter((NOW, NOW + timedelta(milliseconds=5)))
    runner = ForwardSessionRunner(tmp_path / "injected-clock.jsonl",
                                  clock_provider=SessionClockProvider(lambda: next(ticks)))
    _approve_preflight(runner)
    result = runner.run(
        _config(), [_event()],
        decision_fn=lambda visible: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}, "visible_count": len(visible)},
        risk_fn=lambda _: {"approved": True},
        execution_fn=lambda *_: {"status": "MISSED", "receipt_id": "clocked-sim",
                                 "fidelity": "SIMULATED_QUOTE"},
    )
    assert result["decision"]["visible_count"] == 1
    assert result["execution"]["simulated_execution_at_utc"] == (
        NOW + timedelta(milliseconds=5)).isoformat()


def test_injected_decision_clock_excludes_later_available_market_event(tmp_path):
    from financial_brain.forward_edge.session_clock import SessionClockProvider

    runner = ForwardSessionRunner(tmp_path / "future-event.jsonl",
                                  clock_provider=SessionClockProvider(lambda: NOW))
    calls = []
    result = runner.run(
        _config(), [_event(NOW + timedelta(seconds=2))],
        decision_fn=lambda visible: calls.append(visible) or {"side": "NONE"},
        risk_fn=lambda _: {"approved": True}, execution_fn=lambda *_: {},
    )
    assert result["decision_event_count"] == 0
    assert calls == [[]]


def test_one_clock_provider_spans_kite_capture_decision_and_paper_execution(tmp_path):
    from financial_brain.ingest.kite_live import forward_events
    from financial_brain.forward_edge.session_clock import SessionClockProvider

    ticks = iter(NOW + timedelta(milliseconds=step) for step in (1, 2, 3, 4, 5))
    clock = SessionClockProvider(lambda: next(ticks))
    events = forward_events(
        ["INFY"],
        quote_fn=lambda *_: {"NSE:INFY": {
            "timestamp": "2026-11-12T09:30:00+05:30", "last_price": 1500,
        }},
        clock_provider=clock,
    )
    runner = ForwardSessionRunner(tmp_path / "same-provider.jsonl", clock)
    _approve_preflight(runner)
    result = runner.run(
        _config(), events, decision_fn=lambda visible: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}, "count": len(visible)},
        risk_fn=lambda _: {"approved": True},
        execution_fn=lambda *_: {"status": "MISSED", "receipt_id": "shared-clock",
                                 "fidelity": "SIMULATED_QUOTE"},
    )
    assert events[0].received_utc == NOW + timedelta(milliseconds=1)
    assert events[0].available_utc == NOW + timedelta(milliseconds=2)
    assert result["decision"]["count"] == 1
    assert result["execution"]["simulated_execution_at_utc"] == (
        NOW + timedelta(milliseconds=5)).isoformat()


def test_market_polls_can_be_durably_appended_before_session_finalization(tmp_path):
    path = tmp_path / "stream.jsonl"
    config = _config()
    live = ForwardSessionRunner(path)
    live.begin(config)
    assert live.append_events(config, [_event(), _event()]) == 1
    restarted = ForwardSessionRunner(path)
    decision_counts = []
    result = restarted.run(config, [], decision_at=NOW,
                           decision_fn=lambda visible: decision_counts.append(len(visible)) or {"side": "NONE"},
                           risk_fn=lambda _: {"approved": True}, execution_fn=lambda *_: {})
    assert result["event_count"] == 1
    assert decision_counts == [1]


def test_ingest_clock_and_raw_source_hash_survive_durable_event_round_trip(tmp_path):
    path = tmp_path / "ingest-lineage.jsonl"
    runner = ForwardSessionRunner(path)
    event = NormalizedEvent(
        "QUOTE", "NSE:INFY", NOW - timedelta(seconds=3), NOW - timedelta(seconds=2),
        NOW - timedelta(seconds=2), (("raw_sha256", "d" * 64), ("source", "kite_connect")),
        ingested_utc=NOW - timedelta(seconds=1),
    )
    assert runner.append_events(_config(), [event]) == 1
    record, = [r for r in DurableJournal(path).records() if r["t"] == "MARKET_EVENT"]
    assert record["event"]["ingested_utc"] == (NOW - timedelta(seconds=1)).isoformat()
    assert dict(tuple(item) for item in record["event"]["payload"])["raw_sha256"] == "d" * 64
    restarted = ForwardSessionRunner(path)
    result = restarted.run(_config(), [], decision_at=NOW,
                           decision_fn=lambda visible: {"side": "NONE", "visible": len(visible)},
                           risk_fn=lambda _: {"approved": True}, execution_fn=lambda *_: {})
    assert result["decision"]["visible"] == 1
    replayed, = [r for r in DurableJournal(path).records() if r["t"] == "MARKET_EVENT"]
    assert replayed["event"]["ingested_utc"] == record["event"]["ingested_utc"]


def test_closed_session_is_replayed_without_reexecuting(tmp_path):
    journal_path = tmp_path / "session.jsonl"
    runner = ForwardSessionRunner(journal_path)
    calls = []
    kwargs = dict(decision_at=NOW, decision_fn=lambda _: {"side": "NONE"},
                  risk_fn=lambda _: {"approved": True},
                  execution_fn=lambda _, intent, events: calls.append(intent) or {"state": "NO_TRADE"})
    first = runner.run(_config(), [_event()], **kwargs)
    second = runner.run(_config(), [_event()], **kwargs)
    assert first == second
    assert first["status"] == "NO_TRADE" and first["reason"] == "NO_SIGNAL"
    assert len(calls) == 0


def test_no_trade_signal_never_reaches_execution_adapter(tmp_path):
    runner = ForwardSessionRunner(tmp_path / "no-trade.jsonl")
    calls = []
    result = runner.run(_config(), [_event()], decision_at=NOW,
                        decision_fn=lambda _: {"side": "NONE", "reason": "EDGE_BELOW_COST"},
                        risk_fn=lambda _: {"approved": True},
                        execution_fn=lambda *args: calls.append(args) or {"state": "FILLED"})
    assert result["status"] == "NO_TRADE"
    assert result["reason"] == "EDGE_BELOW_COST"
    assert calls == []


def test_run_config_hash_normalizes_capital_and_rejects_duplicate_instruments():
    assert _config().sha256 == "8f9bc7f06ef12a4da7059b0d51e968c170bf99ba7204a1f7a57c421e90435507"
    assert _config(capital_inr="1000000.00").sha256 == _config(capital_inr="1000000").sha256
    assert _config(ledger_id="LEDGER-2").sha256 != _config().sha256
    assert _config(risk_policy_sha256="d" * 64).sha256 != _config().sha256
    with pytest.raises(RunnerError, match="unique"):
        _config(instruments=("NSE:INFY", "NSE:INFY")).canonical()


def test_attempts_append_under_one_session_key_without_overwriting_prior_result(tmp_path):
    path = tmp_path / "attempts.jsonl"
    first = _config(session_id="S-ATTEMPT-1", attempt_id="attempt-1")
    second = _config(session_id="S-ATTEMPT-2", attempt_id="attempt-2")
    assert first.session_key == second.session_key
    assert first.sha256 != second.sha256
    runner = ForwardSessionRunner(path)
    first_result = runner.run(first, [_event()], decision_at=NOW,
                              decision_fn=lambda _: {"side": "NONE", "reason": "FIRST_ATTEMPT"},
                              risk_fn=lambda _: {"approved": True}, execution_fn=lambda *_: {})
    before = [row for row in DurableJournal(path).records() if row.get("session_id") == first.session_id]
    second_result = runner.run(second, [_event()], decision_at=NOW,
                               decision_fn=lambda _: {"side": "NONE", "reason": "SECOND_ATTEMPT"},
                               risk_fn=lambda _: {"approved": True}, execution_fn=lambda *_: {})
    after = [row for row in DurableJournal(path).records() if row.get("session_id") == first.session_id]
    assert before == after
    assert first_result["reason"] == "FIRST_ATTEMPT"
    assert second_result["reason"] == "SECOND_ATTEMPT"
    assert len([row for row in DurableJournal(path).records() if row.get("t") == "SESSION_PLAN"]) == 2
    with pytest.raises(RunnerError, match="attempt_id already exists"):
        runner.begin(_config(session_id="S-ATTEMPT-3", attempt_id="attempt-1"))


def test_pending_outbox_requires_reconciliation_after_unknown_side_effect(tmp_path):
    journal_path = tmp_path / "session.jsonl"
    runner = ForwardSessionRunner(journal_path)
    _approve_preflight(runner)
    executions = {}
    attempts = []

    def execution(_decision, intent_id, _events):
        attempts.append(intent_id)
        if intent_id not in executions:
            executions[intent_id] = {
                "status": "FILLED", "receipt_id": "sim-fill-F-1", "fidelity": "SIMULATED_QUOTE",
                    "simulated_execution_at_utc": (NOW + timedelta(seconds=1)).isoformat(),
                "fills": [{"fill_id": "F-1", "quantity": 10, "price": "100.00"}],
            }
            if len(attempts) == 1:
                raise RuntimeError("simulated process loss after paper side effect")
        return executions[intent_id]

    kwargs = dict(decision_at=NOW, decision_fn=lambda _: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}},
                  risk_fn=lambda _: {"approved": True}, execution_fn=execution)
    with pytest.raises(RuntimeError, match="process loss"):
        runner.run(_config(), [_event()], **kwargs)
    with pytest.raises(ExecutionOutcomeUnknown, match="reconcile"):
        runner.run(_config(), [_event()], **kwargs)
    assert len(attempts) == 1  # restart did not blindly repeat a possibly completed effect
    recovered = runner.run(
        _config(), [_event()], **kwargs,
        reconcile_execution_fn=lambda intent: {
            "status": "APPLIED", "reconciliation_id": "sim-store-check-1", "execution": executions[intent]
        },
    )
    assert recovered["execution"] == executions[attempts[0]]
    records = DurableJournal(journal_path).records()
    assert [r["t"] for r in records].count("PAPER_EXECUTION") == 1
    assert [r["t"] for r in records].count("OUTBOX_ACK") == 1
    assert [r["t"] for r in records].count("OUTBOX_RECONCILED") == 1


def test_outbox_retries_only_after_reconciliation_proves_no_effect(tmp_path):
    path = tmp_path / "retry.jsonl"
    runner = ForwardSessionRunner(path)
    _approve_preflight(runner)
    attempts = []

    def execution(_decision, intent_id, _events):
        attempts.append(intent_id)
        if len(attempts) == 1:
            raise RuntimeError("failed before simulation commit")
        return {"status": "FILLED", "receipt_id": "sim-fill-2", "fidelity": "SIMULATED_BAR",
                "fills": [{"fill_id": "F-2", "quantity": 10, "price": "100.25"}]}

    kwargs = dict(decision_at=NOW, decision_fn=lambda _: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}},
                  risk_fn=lambda _: {"approved": True}, execution_fn=execution)
    with pytest.raises(RuntimeError, match="before simulation"):
        runner.run(_config(), [_event()], **kwargs)
    result = runner.run(
        _config(), [_event()], **kwargs,
        reconcile_execution_fn=lambda intent: {
            "status": "NOT_APPLIED", "reconciliation_id": "sim-store-absence-2"
        },
    )
    assert result["status"] == "PAPER_COMPLETE"
    assert result["execution"]["receipt_id"] == "sim-fill-2"
    assert attempts[0] == attempts[1]


def test_execution_without_simulation_receipt_cannot_close_paper_session(tmp_path):
    path = tmp_path / "missing-receipt.jsonl"
    runner = ForwardSessionRunner(path)
    _approve_preflight(runner)
    with pytest.raises(RunnerError, match="receipt_id"):
        runner.run(
            _config(), [_event()], decision_at=NOW, decision_fn=lambda _: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}},
            risk_fn=lambda _: {"approved": True},
            execution_fn=lambda *_: {"status": "FILLED", "fidelity": "SIMULATED_QUOTE"},
        )
    records = DurableJournal(path).records()
    assert any(r["t"] == "OUTBOX_UNKNOWN" for r in records)
    assert not any(r["t"] in {"PAPER_EXECUTION", "OUTBOX_ACK", "SESSION_CLOSED"} for r in records)


def test_filled_status_without_fill_facts_cannot_be_acknowledged(tmp_path):
    path = tmp_path / "empty-fill.jsonl"
    runner = ForwardSessionRunner(path)
    _approve_preflight(runner)
    with pytest.raises(RunnerError, match="observed fills"):
        runner.run(
            _config(), [_event()], decision_at=NOW, decision_fn=lambda _: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}},
            risk_fn=lambda _: {"approved": True},
            execution_fn=lambda *_: {
                "status": "FILLED", "receipt_id": "sim-empty", "fidelity": "SIMULATED_QUOTE"
            },
        )
    records = DurableJournal(path).records()
    assert not any(r["t"] in {"PAPER_EXECUTION", "OUTBOX_ACK", "SESSION_CLOSED"} for r in records)


def test_restart_after_execution_receipt_before_ack_does_not_repeat_execution(tmp_path, monkeypatch):
    import financial_brain.forward_edge.session_runner as session_module

    path = tmp_path / "receipt-before-ack.jsonl"
    runner = ForwardSessionRunner(path)
    _approve_preflight(runner)
    calls = []
    receipt = {"status": "FILLED", "receipt_id": "sim-fill-crash", "fidelity": "SIMULATED_QUOTE",
               "simulated_execution_at_utc": (NOW + timedelta(seconds=1)).isoformat(),
               "fills": [{"fill_id": "F-3", "quantity": 1, "price": "123.45"}]}
    original_append = session_module.DurableJournal.append
    crashed = False

    def append_then_crash(journal, rec):
        nonlocal crashed
        seq = original_append(journal, rec)
        if rec.get("t") == "PAPER_EXECUTION" and not crashed:
            crashed = True
            raise RuntimeError("crash after receipt fsync")
        return seq

    monkeypatch.setattr(session_module.DurableJournal, "append", append_then_crash)
    kwargs = dict(decision_at=NOW, decision_fn=lambda _: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}},
                  risk_fn=lambda _: {"approved": True},
                  execution_fn=lambda *args: calls.append(args) or receipt)
    with pytest.raises(RuntimeError, match="receipt fsync"):
        runner.run(_config(), [_event()], **kwargs)
    monkeypatch.setattr(session_module.DurableJournal, "append", original_append)
    recovered = runner.run(_config(), [_event()], **kwargs)
    assert recovered["execution"] == receipt
    assert len(calls) == 1
    records = DurableJournal(path).records()
    assert [r["t"] for r in records].count("PAPER_EXECUTION") == 1
    assert [r["t"] for r in records].count("OUTBOX_ACK") == 1


def test_session_id_cannot_be_reused_for_different_configuration(tmp_path):
    runner = ForwardSessionRunner(tmp_path / "session.jsonl")
    runner.run(_config(), [], decision_at=NOW, decision_fn=lambda _: {"side": "NONE"},
                   risk_fn=lambda _: {"approved": False}, execution_fn=lambda *_: {})
    with pytest.raises(RunnerError, match="different"):
        runner.run(_config(cohort_id="C2"), [], decision_at=NOW,
                   decision_fn=lambda _: {}, risk_fn=lambda _: {"approved": False}, execution_fn=lambda *_: {})


def test_runner_rejects_physical_or_naive_clock_modes(tmp_path):
    with pytest.raises(RunnerError, match="only accepts"):
        _config(mode=Mode.LIVE_CASH).canonical()
    journal_path = tmp_path / "j"
    with pytest.raises(RunnerError, match="timezone-aware"):
        ForwardSessionRunner(journal_path).run(
            _config(), [], decision_at=datetime(2026, 11, 12), decision_fn=lambda _: {},
            risk_fn=lambda _: {"approved": False}, execution_fn=lambda *_: {})
    records = DurableJournal(journal_path).records()
    assert any(row["t"] == "SESSION_INCOMPLETE" for row in records)
    assert not any(row["t"] in {"DECISION", "PAPER_INTENT", "SESSION_CLOSED"} for row in records)


def test_non_boolean_risk_approval_is_rejected_without_execution(tmp_path):
    journal_path = tmp_path / "risk.jsonl"
    calls = []
    with pytest.raises(RunnerError, match="boolean approved"):
        ForwardSessionRunner(journal_path).run(
            _config(), [_event()], decision_at=NOW, decision_fn=lambda _: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}},
            risk_fn=lambda _: {"approved": "false"}, execution_fn=lambda *args: calls.append(args) or {})
    assert calls == []
    assert all(r["t"] != "RISK_VERDICT" for r in DurableJournal(journal_path).records())


@pytest.mark.parametrize("decision", [
    {"side": "BUY"},
    {"side": "BUY", "expected_net_alpha": 0.01, "forecast_horizon_sessions": 1,
     "net_alpha_forecast": _net_alpha_forecast()},
    {"side": "BUY", "expected_net_alpha": 0.01, "forecast_horizon_sessions": 1,
     "forecast_costs_included": False, "net_alpha_forecast": _net_alpha_forecast()},
    {"side": "BUY", "expected_net_alpha": 0.01, "forecast_horizon_sessions": 0,
     "forecast_costs_included": True, "net_alpha_forecast": _net_alpha_forecast(horizon=0)},
    {"side": "BUY", "expected_net_alpha": 0.01, "forecast_horizon_sessions": 1,
     "forecast_costs_included": True, "net_alpha_forecast": _net_alpha_forecast()},
    {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1,
     "forecast_costs_included": True, "net_alpha_forecast": _net_alpha_forecast(net=0.01)},
    {"side": "BUY", "expected_net_alpha": -0.001, "forecast_horizon_sessions": 1,
     "forecast_costs_included": True, "net_alpha_forecast": _net_alpha_forecast(net=-0.001)},
    {"side": "SELL", "position_effect": "INCREASE"},
])
def test_paper_entry_hurdle_rejects_missing_or_non_exceeding_forecast(tmp_path, decision):
    path = tmp_path / "net-alpha-gate.jsonl"
    runner = ForwardSessionRunner(path)
    config = _config(minimum_forecast_net_alpha=0.01)
    _approve_preflight(runner, config)
    calls = []
    result = runner.run(
        config, [_event()], decision_at=NOW, decision_fn=lambda _: decision,
        risk_fn=lambda _: {"approved": True},
        execution_fn=lambda *args: calls.append(args) or {},
    )
    assert result["status"] == "NO_TRADE"
    assert result["reason"] == "NET_ALPHA_HURDLE_NOT_MET"
    assert result["decision"]["side"] == "NO_TRADE"
    assert result["decision"]["rejected_candidate"] == decision
    assert result["risk"]["net_alpha_hurdle"]["required"] is True
    assert calls == []
    persisted = [r for r in DurableJournal(path).records() if r["t"] == "RISK_VERDICT"]
    assert persisted[-1]["risk"]["net_alpha_hurdle"] == result["risk"]["net_alpha_hurdle"]
    dispositions = [r for r in DurableJournal(path).records() if r["t"] == "DECISION_DISPOSITION"]
    assert dispositions[-1]["decision"] == result["decision"]


def test_paper_entry_above_hurdle_is_recorded_and_can_reach_simulator(tmp_path):
    path = tmp_path / "net-alpha-pass.jsonl"
    runner = ForwardSessionRunner(path)
    config = _config(minimum_forecast_net_alpha=0.01)
    _approve_preflight(runner, config)
    calls = []
    decision = {"side": "BUY", "expected_net_alpha": 0.02,
                "forecast_horizon_sessions": 3, "forecast_costs_included": True,
                "net_alpha_forecast": _net_alpha_forecast(net=0.02, horizon=3)}
    result = runner.run(
        config, [_event()], decision_at=NOW, decision_fn=lambda _: decision,
        risk_fn=lambda _: {"approved": True},
        execution_fn=lambda *_: calls.append(True) or {
            "status": "MISSED", "receipt_id": "sim-missed-gate",
            "fidelity": "SIMULATED_QUOTE",
        },
    )
    assert result["status"] == "PAPER_COMPLETE"
    assert result["risk"]["net_alpha_hurdle"]["passed"] is True
    assert len(calls) == 1


def test_paper_position_reduction_is_exempt_but_short_entry_needs_hurdle(tmp_path):
    runner = ForwardSessionRunner(tmp_path / "position-effects.jsonl")
    _approve_preflight(runner)
    reductions = runner.run(
        _config(), [_event()], decision_at=NOW,
        decision_fn=lambda _: {"side": "SELL", "position_effect": "REDUCE"},
        risk_fn=lambda _: {"approved": True},
        execution_fn=lambda *_: {"status": "MISSED", "receipt_id": "sim-reduce",
                                 "fidelity": "SIMULATED_QUOTE"},
    )
    assert reductions["risk"]["net_alpha_hurdle"]["required"] is False

    short_runner = ForwardSessionRunner(tmp_path / "short-entry.jsonl")
    short_entry = short_runner.run(
        _config(session_id="short-entry"), [_event()], decision_at=NOW,
        decision_fn=lambda _: {"side": "SELL", "position_effect": "INCREASE"},
        risk_fn=lambda _: {"approved": True}, execution_fn=lambda *_: {},
    )
    assert short_entry["status"] == "NO_TRADE"
    assert short_entry["reason"] == "NET_ALPHA_HURDLE_NOT_MET"


def test_risk_rejection_is_terminal_and_never_executes(tmp_path):
    journal_path = tmp_path / "rejected.jsonl"
    calls = []
    result = ForwardSessionRunner(journal_path).run(
        _config(), [_event()], decision_at=NOW, decision_fn=lambda _: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}},
        risk_fn=lambda _: {"approved": False, "reason": "MAX_EXPOSURE"},
        execution_fn=lambda *args: calls.append(args) or {"state": "FILLED"})
    assert result["status"] == "REJECTED" and result["reason"] == "MAX_EXPOSURE"
    assert calls == []
    states = [r["state"] for r in DurableJournal(journal_path).records() if r["t"] == "SESSION_STATE"]
    assert states[-1] == "REJECTED"


def test_actionable_decision_without_preflight_is_rejected_and_not_executed(tmp_path):
    path = tmp_path / "missing-preflight.jsonl"
    calls = []
    result = ForwardSessionRunner(path).run(
        _config(), [_event()], decision_at=NOW, decision_fn=lambda _: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}},
        risk_fn=lambda _: {"approved": True},
        execution_fn=lambda *args: calls.append(args) or {},
    )
    records = DurableJournal(path).records()
    assert result["status"] == "REJECTED" and result["reason"] == "PREFLIGHT_NOT_RECORDED"
    assert result["preflight"]["status"] == "MISSING"
    assert calls == []
    assert not any(r["t"] == "OUTBOX_PENDING" for r in records)


def test_data_rejections_are_immutable_idempotent_and_persisted(tmp_path):
    path = tmp_path / "rejections.jsonl"
    runner = ForwardSessionRunner(path)
    rejection = {"event_sha256": "e" * 64, "instrument": "NSE:INFY", "reason": "STALE_QUOTE"}
    assert runner.record_data_rejections(_config(), [rejection, rejection]) == 1
    assert runner.record_data_rejections(_config(), [rejection]) == 0
    with pytest.raises(RunnerError, match="different reason"):
        runner.record_data_rejections(_config(), [{**rejection, "reason": "BAD_BOOK"}])
    persisted = [r for r in DurableJournal(path).records() if r["t"] == "DATA_REJECTION"]
    assert persisted == [{"t": "DATA_REJECTION", "session_id": "S-1", **rejection}]


def test_preflight_is_immutable_and_blocks_actionable_decisions(tmp_path):
    path = tmp_path / "failed-preflight.jsonl"
    runner = ForwardSessionRunner(path)
    result = {"approved": False, "status": "FAIL", "reason": "STALE_FEED"}
    runner.record_preflight(_config(), result)
    runner.record_preflight(_config(), result)
    with pytest.raises(RunnerError, match="immutable"):
        runner.record_preflight(_config(), {"approved": True, "status": "PASS"})
    calls = []
    outcome = runner.run(
        _config(), [_event()], decision_at=NOW, decision_fn=lambda _: {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}},
        risk_fn=lambda _: {"approved": True},
        execution_fn=lambda *args: calls.append(args) or {},
    )
    assert outcome["status"] == "REJECTED" and outcome["reason"] == "STALE_FEED"
    assert calls == []


def test_session_restart_rejects_changed_decision_timestamp(tmp_path):
    runner = ForwardSessionRunner(tmp_path / "clock.jsonl")
    kwargs = dict(decision_fn=lambda _: {"side": "NONE"}, risk_fn=lambda _: {"approved": True},
                  execution_fn=lambda *_: {})
    runner.run(_config(), [_event()], decision_at=NOW, **kwargs)
    with pytest.raises(RunnerError, match="different decision timestamp"):
        runner.run(_config(), [_event()], decision_at=NOW + timedelta(seconds=1), **kwargs)


def test_incomplete_session_is_preserved_and_cannot_be_reused(tmp_path):
    journal_path = tmp_path / "incomplete.jsonl"
    runner = ForwardSessionRunner(journal_path)
    with pytest.raises(RuntimeError, match="strategy outage"):
        runner.run(_config(), [_event()], decision_at=NOW,
                   decision_fn=lambda _: (_ for _ in ()).throw(RuntimeError("strategy outage")),
                   risk_fn=lambda _: {"approved": True}, execution_fn=lambda *_: {})
    runner.mark_incomplete("S-1", "strategy outage")
    records = DurableJournal(journal_path).records()
    assert [r["state"] for r in records if r["t"] == "SESSION_STATE"][-1] == "INCOMPLETE"
    with pytest.raises(RunnerError, match="terminal"):
        runner.run(_config(), [_event()], decision_at=NOW,
                   decision_fn=lambda _: {"side": "NONE"}, risk_fn=lambda _: {"approved": True},
                   execution_fn=lambda *_: {})


def test_stale_feed_marks_session_incomplete_and_never_calls_strategy_or_execution(tmp_path):
    path = tmp_path / "stale-feed-incomplete.jsonl"
    runner = ForwardSessionRunner(path)
    config = _config(require_feed_health=True)
    _approve_preflight(runner, config)
    runner.record_feed_health(
        config,
        FeedHealth(last_available_utc=NOW - timedelta(seconds=10), gaps=0, disconnects=0,
                   dropped_updates=0, last_heartbeat_utc=NOW - timedelta(seconds=10)),
        observed_at=NOW,
    )
    should_not_run = lambda *_: pytest.fail("feed outage must block new decisions and orders")
    result = runner.run(config, [_event()], decision_at=NOW,
                        decision_fn=should_not_run, risk_fn=should_not_run,
                        execution_fn=should_not_run)
    records = DurableJournal(path).records()
    incomplete = next(row for row in records if row["t"] == "SESSION_INCOMPLETE")
    assert result["status"] == "INCOMPLETE"
    assert result["reason"] == "FEED_ENDED_EARLY"
    assert incomplete["evidence"]["blocking_reasons"] == ["STALE_QUOTE", "STALE_HEARTBEAT"]
    assert not any(row["t"] in {"DECISION", "PAPER_INTENT", "SESSION_CLOSED"} for row in records)
    assert [row["state"] for row in records if row["t"] == "SESSION_STATE"][-1] == "INCOMPLETE"


def test_required_but_missing_feed_health_is_not_a_successful_no_trade(tmp_path):
    runner = ForwardSessionRunner(tmp_path / "missing-health.jsonl")
    config = _config(require_feed_health=True)
    _approve_preflight(runner, config)
    blocked = lambda *_: pytest.fail("missing required feed health must fail closed")
    result = runner.run(config, [_event()], decision_at=NOW,
                        decision_fn=blocked, risk_fn=blocked, execution_fn=blocked)
    assert result["status"] == "INCOMPLETE"
    assert result["reason"] == "FEED_ENDED_EARLY"
    assert result["feed_health"]["blocking_reasons"] == ["FEED_HEALTH_MISSING"]


def test_untrusted_decision_clock_creates_incomplete_evidence_without_trade(tmp_path):
    path = tmp_path / "bad-clock.jsonl"
    runner = ForwardSessionRunner(path, SessionClockProvider(lambda: datetime(2026, 11, 12, 9, 0)))
    blocked = lambda *_: pytest.fail("untrusted clock must fail before decision or execution")
    with pytest.raises(RunnerError, match="marked incomplete"):
        runner.run(_config(), [_event()], decision_fn=blocked, risk_fn=blocked, execution_fn=blocked)
    records = DurableJournal(path).records()
    incomplete = next(row for row in records if row["t"] == "SESSION_INCOMPLETE")
    assert incomplete["evidence"] == {
        "clock_trusted": False, "phase": "DECISION_CLOCK", "clock_error_type": "ClockError",
    }
    assert not any(row["t"] in {"DECISION", "PAPER_INTENT", "SESSION_CLOSED"} for row in records)


def test_execution_clock_failure_keeps_outbox_unknown_and_never_closes_as_zero_loss(tmp_path):
    path = tmp_path / "execution-clock-failure.jsonl"
    runner = ForwardSessionRunner(path, SessionClockProvider(lambda: datetime(2026, 11, 12, 9, 0)))
    config = _config()
    _approve_preflight(runner, config)
    forecast = {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01,
                "fees": 0.001, "spread": 0.001, "impact": 0.001}
    with pytest.raises(RunnerError, match="timezone-aware"):
        runner.run(
            config, [_event()], decision_at=NOW,
            decision_fn=lambda _: {"side": "BUY", "expected_net_alpha": 0.02,
                                   "forecast_horizon_sessions": 1,
                                   "forecast_costs_included": True,
                                   "net_alpha_forecast": forecast},
            risk_fn=lambda _: {"approved": True},
            execution_fn=lambda *_: {"status": "MISSED", "receipt_id": "clockless",
                                     "fidelity": "SIMULATED_QUOTE"},
        )
    records = DurableJournal(path).records()
    assert any(row["t"] == "OUTBOX_UNKNOWN" for row in records)
    assert any(row["t"] == "PAPER_INTENT" for row in records)
    assert not any(row["t"] in {"PAPER_EXECUTION", "OUTBOX_ACK", "SESSION_CLOSED"}
                   for row in records)


def test_invalid_state_transition_fails_closed(tmp_path):
    journal = DurableJournal(tmp_path / "state.jsonl")
    journal.append({"t": "SESSION_PLAN", "session_id": "S-1"})
    ForwardSessionRunner._ensure_state(journal, "S-1", "PLANNED")
    with pytest.raises(RunnerError, match="invalid session transition"):
        ForwardSessionRunner._ensure_state(journal, "S-1", "OPEN")


def test_paper_runner_applies_frozen_uncertainty_haircut_to_order_quantity(tmp_path):
    policy = _sizing_policy()
    registry = UncertaintySizingRegistry(tmp_path / "sizing-policy.jsonl")
    registry.freeze(policy)
    config = _config(uncertainty_sizing_policy_sha256=policy.sha256)
    runner = ForwardSessionRunner(tmp_path / "sized-paper.jsonl", uncertainty_sizing_registry=registry)
    _approve_preflight(runner, config)
    observed_risk_quantities = []
    observed_order_quantities = []
    decision = {"side": "BUY", "quantity": 10, "expected_net_alpha": 0.01,
                "uncertainty_standard_error": 0.005, "forecast_horizon_sessions": 1,
                "forecast_costs_included": True, "net_alpha_forecast": _net_alpha_forecast()}
    result = runner.run(
        config, [_event()], decision_at=NOW, decision_fn=lambda _: decision,
        risk_fn=lambda value: observed_risk_quantities.append(value["quantity"]) or {"approved": True},
        execution_fn=lambda value, *_: observed_order_quantities.append(value["quantity"]) or {
            "status": "MISSED", "receipt_id": "uncertainty-missed", "fidelity": "SIMULATED_QUOTE"},
    )
    assert result["status"] == "PAPER_COMPLETE"
    assert result["decision"]["quantity"] == 5
    assert result["decision"]["uncertainty_sizing"]["size_multiplier"] == pytest.approx(0.5)
    assert observed_risk_quantities == [5]
    assert observed_order_quantities == [5]


def test_uncertainty_policy_fails_closed_on_missing_error_or_subunit_size(tmp_path):
    policy = _sizing_policy()
    registry = UncertaintySizingRegistry(tmp_path / "sizing-policy.jsonl")
    registry.freeze(policy)
    config = _config(uncertainty_sizing_policy_sha256=policy.sha256)
    runner = ForwardSessionRunner(tmp_path / "missing-uncertainty.jsonl", uncertainty_sizing_registry=registry)
    _approve_preflight(runner, config)
    executions = []
    result = runner.run(
        config, [_event()], decision_at=NOW,
        decision_fn=lambda _: {"side": "BUY", "quantity": 10, "expected_net_alpha": 0.01},
        risk_fn=lambda _: {"approved": True},
        execution_fn=lambda *_: executions.append(True) or {},
    )
    assert result["status"] == "NO_TRADE"
    assert result["reason"] == "UNCERTAINTY_SIZING_EVIDENCE_MISSING"
    assert executions == []


def test_paper_runner_rejects_unbound_or_prevalidation_uncertainty_policy(tmp_path):
    policy = _sizing_policy()
    registry = UncertaintySizingRegistry(tmp_path / "sizing-policy.jsonl")
    registry.freeze(policy)
    runner = ForwardSessionRunner(tmp_path / "policy-binding.jsonl", uncertainty_sizing_registry=registry)
    with pytest.raises(RunnerError, match="needs a frozen uncertainty-sizing policy"):
        runner.begin(_config())
    with pytest.raises(RunnerError, match="validation start"):
        runner.begin(_config(exchange_date="2026-11-11",
                             uncertainty_sizing_policy_sha256=policy.sha256))


def test_runner_applies_frozen_cost_to_calibrated_benefit_gate(tmp_path):
    policy = _benefit_policy()
    registry = ForecastBenefitPolicyRegistry(tmp_path / "forecast-benefit.jsonl")
    registry.freeze(policy)
    config = _config(forecast_benefit_policy_sha256=policy.sha256)
    runner = ForwardSessionRunner(tmp_path / "benefit-paper.jsonl", forecast_benefit_registry=registry)
    _approve_preflight(runner, config)
    risk_alpha = []
    result = runner.run(
        config, [_event()], decision_at=NOW,
        decision_fn=lambda _: {"side": "BUY", "expected_net_alpha": 0.01,
                               "forecast_horizon_sessions": 1, "forecast_costs_included": True,
                               "forecast_model_sha256": "d" * 64,
                               "net_alpha_forecast": _net_alpha_forecast()},
        risk_fn=lambda decision: risk_alpha.append(decision["expected_net_alpha"]) or {"approved": True},
        execution_fn=lambda *_: {"status": "MISSED", "receipt_id": "cost-benefit-missed",
                                 "fidelity": "SIMULATED_QUOTE"},
    )
    assert result["status"] == "PAPER_COMPLETE"
    assert risk_alpha == [pytest.approx(0.0035)]
    assert result["decision"]["cost_benefit_assessment"]["ratio"] == pytest.approx(0.003 / 0.0065)


def test_placeholder_zero_net_alpha_is_rejected_after_calibrated_cost_ratio(tmp_path):
    policy = _benefit_policy()
    registry = ForecastBenefitPolicyRegistry(tmp_path / "forecast-benefit.jsonl")
    registry.freeze(policy)
    config = _config(forecast_benefit_policy_sha256=policy.sha256)
    runner = ForwardSessionRunner(tmp_path / "zero-benefit.jsonl", forecast_benefit_registry=registry)
    _approve_preflight(runner, config)
    executions = []
    result = runner.run(
        config, [_event()], decision_at=NOW,
        decision_fn=lambda _: {"side": "BUY", "expected_net_alpha": 0.0,
                               "forecast_horizon_sessions": 1, "forecast_costs_included": True,
                               "forecast_model_sha256": "d" * 64,
                               "net_alpha_forecast": _net_alpha_forecast(net=0.0)},
        risk_fn=lambda _: {"approved": True},
        execution_fn=lambda *_: executions.append(True) or {},
    )
    assert result["status"] == "NO_TRADE"
    assert result["reason"] == "COST_EXCEEDS_CALIBRATED_BENEFIT"
    assert executions == []


def test_runner_requires_frozen_benefit_policy_identity_for_configured_paper(tmp_path):
    policy = _benefit_policy()
    registry = ForecastBenefitPolicyRegistry(tmp_path / "forecast-benefit.jsonl")
    registry.freeze(policy)
    runner = ForwardSessionRunner(tmp_path / "missing-benefit-policy.jsonl", forecast_benefit_registry=registry)
    with pytest.raises(RunnerError, match="frozen forecast-benefit"):
        runner.begin(_config())


def _degradation_policy(strategy_version="a" * 64):
    return FrozenThresholds(
        strategy_id="test-strategy", strategy_version=strategy_version,
        frozen_at=NOW - timedelta(days=3), cohort_starts_at=NOW - timedelta(days=2),
        pause_net_alpha_bps=0.0, retire_net_alpha_bps=-20.0,
        max_slippage_bps=12.0, min_fill_coverage=0.75, max_drift_score=0.30,
        min_observations=1, pause_after_breaches=1, retire_after_severe_breaches=3,
    )


def _degradation_observation(policy, observation_id, *, alpha=5.0):
    end = NOW + timedelta(minutes=1)
    return RollingObservation(
        observation_id=observation_id, policy_sha256=policy.policy_sha256,
        window_start=policy.cohort_starts_at, window_end=end,
        observed_at=end + timedelta(minutes=1), net_alpha_bps=alpha,
        slippage_bps=5.0, fill_coverage=1.0, drift_score=0.0,
        sample_count=1, evidence_ids=(f"evidence:{observation_id}",),
    )


def test_runner_persists_closed_session_degradation_observation(tmp_path):
    policy = _degradation_policy()
    config = _config(degradation_policy_sha256=policy.policy_sha256)
    journal_path = tmp_path / "session.jsonl"
    runner = ForwardSessionRunner(journal_path)
    observed_sessions = []

    def observe(run_config, _result, _records):
        observed_sessions.append(run_config.session_id)
        return _degradation_observation(policy, run_config.session_id)

    result = runner.run(
        config, [], decision_at=NOW,
        decision_fn=lambda _: {"side": "NONE", "reason": "NO_SIGNAL"},
        risk_fn=lambda _: {"approved": True}, execution_fn=lambda *_: pytest.fail("no intent"),
        degradation_policy=policy,
        degradation_observation_fn=observe,
    )
    assert result["status"] == "NO_TRADE"
    assert result["degradation"]["action"] == DegradationAction.CONTINUE.value
    alarm = runner.record_degradation_observation(
        config, policy, _degradation_observation(policy, config.session_id),
    )
    assert alarm.action == DegradationAction.CONTINUE
    with pytest.raises(RunnerError, match="conflicts with its persisted"):
        runner.record_degradation_observation(
            config, policy, _degradation_observation(policy, config.session_id, alpha=6.0),
        )
    replay = runner.run(
        config, [], decision_at=NOW,
        decision_fn=lambda _: pytest.fail("closed session must not rerun decisions"),
        risk_fn=lambda _: pytest.fail("closed session must not rerun risk"),
        execution_fn=lambda *_: pytest.fail("closed session must not execute"),
        degradation_policy=policy, degradation_observation_fn=observe,
    )
    assert replay["degradation"] == result["degradation"]
    assert observed_sessions == [config.session_id]
    assert any(r["t"] == "DEGRADATION_ALARM" and r["observation_id"] == config.session_id
               for r in DurableJournal(journal_path).records())


def test_runner_rejects_degradation_policy_for_another_strategy_version(tmp_path):
    policy = _degradation_policy("other-version")
    config = _config(degradation_policy_sha256=policy.policy_sha256)
    journal_path = tmp_path / "session.jsonl"
    runner = ForwardSessionRunner(journal_path)
    with pytest.raises(RunnerError, match="strategy digest"):
        runner.run(
            config, [], decision_at=NOW,
            decision_fn=lambda _: {"side": "NONE"}, risk_fn=lambda _: {"approved": True},
            execution_fn=lambda *_: {}, degradation_policy=policy,
            degradation_observation_fn=lambda run_config, _result, _records:
                _degradation_observation(policy, run_config.session_id),
        )
    assert not journal_path.exists()


def test_durable_degradation_pause_blocks_next_runner_candidate(tmp_path):
    policy = _degradation_policy()
    journal_path = tmp_path / "session.jsonl"
    monitor = DegradationMonitor(policy, journal=DurableJournal(journal_path))
    assert monitor.observe(_degradation_observation(policy, "prior-session", alpha=-1.0)).action == \
        DegradationAction.PAUSE

    config = _config(session_id="S-2", exchange_date="2026-11-13",
                     degradation_policy_sha256=policy.policy_sha256)
    runner = ForwardSessionRunner(journal_path)
    result = runner.run(
        config, [], decision_at=NOW + timedelta(days=1),
        decision_fn=lambda _: {"side": "BUY", "position_effect": "INCREASE"},
        risk_fn=lambda _: {"approved": True},
        execution_fn=lambda *_: pytest.fail("paused strategy must not create an intent"),
        degradation_policy=policy,
        degradation_observation_fn=lambda run_config, _result, _records:
            _degradation_observation(policy, run_config.session_id),
    )
    assert result["status"] == "NO_TRADE"
    assert result["reason"] == "DEGRADATION_PAUSE"
    assert result["decision"]["side"] == "NO_TRADE"
    assert result["decision"]["rejected_candidate"]["position_effect"] == "INCREASE"
    records = DurableJournal(journal_path).records()
    assert not any(r.get("t") == "PAPER_INTENT" and r.get("session_id") == config.session_id
                   for r in records)


def test_durable_degradation_pause_still_allows_explicit_position_close(tmp_path):
    policy = _degradation_policy()
    journal_path = tmp_path / "session.jsonl"
    monitor = DegradationMonitor(policy, journal=DurableJournal(journal_path))
    monitor.observe(_degradation_observation(policy, "prior-session", alpha=-1.0))
    config = _config(session_id="S-2", exchange_date="2026-11-13",
                     degradation_policy_sha256=policy.policy_sha256)
    runner = ForwardSessionRunner(journal_path)
    _approve_preflight(runner, config)
    result = runner.run(
        config, [], decision_at=NOW + timedelta(days=1),
        decision_fn=lambda _: {"side": "SELL", "position_effect": "CLOSE", "quantity": 1},
        risk_fn=lambda _: {"approved": True},
        execution_fn=lambda *_: {"status": "MISSED", "receipt_id": "close-missed",
                                 "fidelity": "SIMULATED_QUOTE"},
        degradation_policy=policy,
        degradation_observation_fn=lambda run_config, _result, _records:
            _degradation_observation(policy, run_config.session_id),
    )
    assert result["status"] == "PAPER_COMPLETE"
    assert result["decision"]["position_effect"] == "CLOSE"
    assert result["execution"]["status"] == "MISSED"
