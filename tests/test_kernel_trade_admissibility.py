from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.costs.india import CostModel, Segment
from financial_brain.forward_edge.cost_schedule import CostSchedule
from financial_brain.forward_edge.parameter_freeze import StrategyFreeze, StrategyFreezeRegistry
from financial_brain.kernel.entry_constraints import EntryConstraintPolicy, evaluate_entry_constraints
from financial_brain.kernel.execution_feasibility import ExecutionEvidence
from financial_brain.kernel.net_ev import FillScenario, NetEVCalibration, NetEVOutcome, net_ev
from financial_brain.kernel.trade_economics_receipt import CashBenchmark, TradeReceiptError, build_trade_economics_receipt
from financial_brain.kernel.trade_admissibility import (
    DecisionReason,
    DecisionState,
    FrozenDecisionContext,
    TradeHurdle,
    evaluate_trade_admissibility,
)
from financial_brain.kernel.trader_signal import (
    DeskId, EntryCondition, EvidenceTier, ExitTrigger, PriceBasis, SignalSide, TradePlan,
    TraderSignal,
)


DECISION = datetime(2026, 11, 23, 18, 30, tzinfo=timezone.utc)
HASH = "a" * 64
PARAMS = "b" * 64
MODEL = "c" * 64


def _schedule():
    return CostSchedule(
        schedule_id="paper-costs", version="v1", effective_from=date(2026, 11, 24),
        effective_to=None, segment=Segment.DELIVERY, model=CostModel(),
        source_ref="tests/paper-cost-fixture", source_sha256=HASH,
    )


def _signal(side=SignalSide.BUY):
    return TraderSignal(
        signal_id="4dbe4c48-7104-4f47-b84c-14a55cd49f24", desk_id=DeskId.SWING,
        strategy_id="momentum", strategy_version="v1", strategy_hash=HASH,
        instrument_id="NSE:INFY", side=side,
        as_of=DECISION - timedelta(seconds=1), decision_at=DECISION,
        expires_at=DECISION + timedelta(minutes=5), evidence_tier=EvidenceTier.REPLAY,
        source_hash=HASH,
    )


def _estimate(*, positive=True, calibrated=True):
    outcomes = (
        NetEVOutcome(
            name="filled", probability=Decimal("0.8"),
            gross_pnl_inr=Decimal("100" if positive else "10"),
            entry_turnover_inr=Decimal("10000"), exit_turnover_inr=Decimal("10200"),
            spread_cost_inr=Decimal("2"), filled_quantity=Decimal("10"),
            visible_quantity=Decimal("20"), liquidity_bucket="large",
            historical_gross_pnl_samples_inr=tuple(Decimal(100 + i) for i in range(35)) if calibrated else (),
        ),
        NetEVOutcome(
            name="no_fill", probability=Decimal("0.2"), gross_pnl_inr=Decimal(0),
            entry_turnover_inr=Decimal(0), exit_turnover_inr=Decimal(0),
            spread_cost_inr=Decimal(0), filled_quantity=Decimal(0), visible_quantity=Decimal(0),
            liquidity_bucket="large",
            historical_gross_pnl_samples_inr=tuple(Decimal(0) for _ in range(35)) if calibrated else (),
        ),
    )
    event_at = DECISION - timedelta(milliseconds=100)
    scenario = FillScenario(
        outcomes=outcomes, instrument_id="NSE:INFY", bid=Decimal("100"), ask=Decimal("100.1"),
        quote_event_at=event_at, quote_available_at=event_at + timedelta(milliseconds=10),
        max_quote_age_ms=500, quote_source_id="KITE_READ_ONLY", quote_source_sha256=HASH,
        calibration=NetEVCalibration("holdout-v1", HASH, True) if calibrated else None,
    )
    return net_ev(_signal(), scenario, _schedule())


def _execution(**changes):
    values = dict(
        bid=Decimal("100"), ask=Decimal("100.1"), quote_age_ms=90, max_quote_age_ms=500,
        spread_bps=Decimal("10"), max_spread_bps=Decimal("20"),
        estimated_slippage_bps=Decimal("2"), max_slippage_bps=Decimal("10"),
        visible_quantity=Decimal("20"), required_quantity=Decimal("10"),
        quote_source_id="KITE_READ_ONLY", quote_source_hash=HASH,
        quote_event_at=DECISION - timedelta(milliseconds=100),
        quote_received_at=DECISION - timedelta(milliseconds=90), observed_at=DECISION,
    )
    return ExecutionEvidence(**(values | changes))


def _context(frozen, **changes):
    values = dict(
        cohort_id="cohort-v1", cohort_sha256=frozen.digest(), session_date=date(2026, 11, 24),
        session_evidence_sha256=HASH, session_eligible=True,
        accepted_instrument_ids=frozenset({"NSE:INFY"}), code_sha256=HASH,
        parameters_sha256=PARAMS, model_sha256=MODEL,
        available_budget_inr=Decimal("100000"), required_budget_inr=Decimal("10000"),
        approval_granted=True, volatility_allowed=True,
        portfolio_utility_delta_inr=Decimal("25"),
    )
    return FrozenDecisionContext(**(values | changes))


def _frozen():
    return StrategyFreeze(
        "cohort-v1", "momentum-v1", "momentum", HASH, PARAMS, MODEL,
        datetime(2026, 11, 22, 12, tzinfo=timezone.utc), date(2026, 11, 24),
    )


def _economic_receipt(signal_value, execution_value):
    plan = TradePlan(
        instrument_id=signal_value.instrument_id, side=signal_value.side.value,
        price_basis=PriceBasis.UNDERLYING, entry_condition=EntryCondition.LIMIT_ZONE,
        entry_low=Decimal("100"), entry_high=Decimal("101"), stop_price=Decimal("95"),
        target_price=Decimal("110"), invalidation="close below support", max_holding_sessions=3,
        tick_size=Decimal("0.05"), lot_size=Decimal("1"), unit_precision=0,
        quantity=Decimal("10"), entry_expires_at=signal_value.expires_at,
        exit_triggers=(ExitTrigger.HARD_STOP, ExitTrigger.TAKE_PROFIT), paper_execution_eligible=True,
    )
    entry = evaluate_entry_constraints(
        signal=signal_value, plan=plan,
        policy=EntryConstraintPolicy("swing", "v1", 300, Decimal("20"), Decimal("50"), Decimal("10")),
        execution=execution_value, decision_reference_price=Decimal("100"),
        evaluated_at=signal_value.decision_at,
    )
    return build_trade_economics_receipt(
        signal=signal_value, plan=plan, execution=execution_value, entry_decision=entry,
        cost_schedule=_schedule(),
        cash_benchmark=CashBenchmark("cash-v1", "v1", Decimal("0.05"), date(2026, 1, 1),
                                     None, "tests/cash-fixture", HASH),
        liquidity_bucket="large", holding_calendar_days=5,
    )


def _evaluate(registry, frozen, *, signal=None, estimate=None, execution=None, context=None,
              no_estimate=False, no_receipt=False):
    signal_value = signal or _signal()
    execution_value = execution or _execution()
    try:
        receipt = None if no_receipt else _economic_receipt(signal_value, execution_value)
    except (TradeReceiptError, ValueError):
        receipt = None
    return evaluate_trade_admissibility(
        signal=signal_value, estimate=None if no_estimate else (estimate or _estimate()),
        economic_receipt=receipt, execution=execution_value,
        hurdle=TradeHurdle("momentum", "v1", "hurdle-v1", Decimal("1"), Decimal("2")),
        context=context or _context(frozen), freeze_registry=registry,
    )


def test_frozen_cohort_and_all_evidence_can_admit_a_paper_candidate(tmp_path):
    registry = StrategyFreezeRegistry(tmp_path / "freeze.sqlite3")
    frozen = _frozen()
    registry.freeze(frozen)

    decision = _evaluate(registry, frozen)

    assert decision.state is DecisionState.TRADE
    assert not decision.reasons
    assert decision.is_broker_authorization is False
    registry.close()


def test_positive_gross_edge_that_does_not_cover_costs_is_rejected(tmp_path):
    registry = StrategyFreezeRegistry(tmp_path / "freeze.sqlite3")
    frozen = _frozen()
    registry.freeze(frozen)

    decision = _evaluate(registry, frozen, estimate=_estimate(positive=False))

    assert decision.state is DecisionState.REJECT
    assert DecisionReason.COSTS_EXCEED_GROSS_EDGE in decision.reasons
    assert DecisionReason.NO_EDGE in decision.reasons
    registry.close()


@pytest.mark.parametrize(
    ("execution", "expected_state", "expected"),
    [
        (_execution(quote_age_ms=900, observed_at=DECISION + timedelta(milliseconds=810)), DecisionState.WATCH, DecisionReason.STALE_FEED),
        (_execution(market_halted=True), DecisionState.REJECT, DecisionReason.INSTRUMENT_HALTED),
    ],
)
def test_stale_feed_and_halted_instrument_are_reason_coded(tmp_path, execution, expected_state, expected):
    registry = StrategyFreezeRegistry(tmp_path / "freeze.sqlite3")
    frozen = _frozen()
    registry.freeze(frozen)

    decision = _evaluate(registry, frozen, execution=execution)

    assert decision.state is expected_state
    assert expected in decision.reasons
    registry.close()


def test_missing_calibration_is_watch_and_not_trade(tmp_path):
    registry = StrategyFreezeRegistry(tmp_path / "freeze.sqlite3")
    frozen = _frozen()
    registry.freeze(frozen)

    decision = _evaluate(registry, frozen, estimate=_estimate(calibrated=False))

    assert decision.state is DecisionState.WATCH
    assert DecisionReason.CONFIDENCE_UNAVAILABLE in decision.reasons
    registry.close()


def test_missing_net_ev_costed_quote_estimate_cannot_become_trade(tmp_path):
    registry = StrategyFreezeRegistry(tmp_path / "freeze.sqlite3")
    frozen = _frozen()
    registry.freeze(frozen)

    decision = _evaluate(registry, frozen, no_estimate=True)

    assert decision.state is DecisionState.WATCH
    assert DecisionReason.ESTIMATE_UNAVAILABLE in decision.reasons
    registry.close()


def test_missing_decision_time_economics_receipt_cannot_become_trade(tmp_path):
    registry = StrategyFreezeRegistry(tmp_path / "freeze.sqlite3")
    frozen = _frozen()
    registry.freeze(frozen)

    decision = _evaluate(registry, frozen, no_receipt=True)

    assert decision.state is DecisionState.WATCH
    assert DecisionReason.ECONOMIC_RECEIPT_UNAVAILABLE in decision.reasons
    registry.close()


def test_session_instrument_budget_approval_and_frozen_parameters_fail_closed(tmp_path):
    registry = StrategyFreezeRegistry(tmp_path / "freeze.sqlite3")
    frozen = _frozen()
    registry.freeze(frozen)
    context = _context(
        frozen, session_eligible=False, accepted_instrument_ids=frozenset({"NSE:TCS"}),
        available_budget_inr=Decimal("1"), approval_granted=False,
        parameters_sha256="d" * 64,
    )

    decision = _evaluate(registry, frozen, context=context)

    assert decision.state is DecisionState.REJECT
    assert {
        DecisionReason.SESSION_INELIGIBLE, DecisionReason.INSTRUMENT_NOT_ACCEPTED,
        DecisionReason.BUDGET_INSUFFICIENT, DecisionReason.APPROVAL_MISSING,
        DecisionReason.COHORT_IDENTITY_INVALID,
    }.issubset(set(decision.reasons))
    registry.close()


def test_portfolio_harm_keeps_capital_in_cash(tmp_path):
    registry = StrategyFreezeRegistry(tmp_path / "freeze.sqlite3")
    frozen = _frozen()
    registry.freeze(frozen)

    decision = _evaluate(
        registry, frozen,
        context=_context(frozen, portfolio_utility_delta_inr=Decimal("-1")),
    )

    assert decision.state is DecisionState.CASH
    assert DecisionReason.MARGINAL_PORTFOLIO_HARM in decision.reasons
    registry.close()


def test_no_trade_signal_is_explicit_cash_outcome(tmp_path):
    registry = StrategyFreezeRegistry(tmp_path / "freeze.sqlite3")
    frozen = _frozen()
    registry.freeze(frozen)

    decision = _evaluate(registry, frozen, signal=_signal(SignalSide.NO_TRADE), estimate=None)

    assert decision.state is DecisionState.CASH
    assert decision.reasons == (DecisionReason.NO_EDGE,)
    registry.close()
