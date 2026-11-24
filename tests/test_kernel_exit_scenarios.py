from datetime import datetime, timedelta, timezone
from dataclasses import replace
from decimal import Decimal

import pytest

from financial_brain.kernel.exit_policy import ExitPolicy, ExitRule, ExitRuleAction
from financial_brain.kernel.exit_scenarios import (
    ExitCostSchedule, ExitPathSample, ExitReplayBar, ExitScenarioError, compare_exit_variants,
    estimate_exit_probabilities,
)
from financial_brain.kernel.trader_signal import (
    DeskId, EntryCondition, EvidenceTier, ExitTrigger, PriceBasis, SignalSide,
    TradePlan, TraderSignal,
)


DECISION = datetime(2026, 11, 23, 18, 30, tzinfo=timezone.utc)
HASH = "a" * 64


def signal():
    return TraderSignal(
        signal_id="e043444d-c290-4fb4-bf0a-d6941fd168a3", desk_id=DeskId.SWING,
        strategy_id="momentum", strategy_version="v1", strategy_hash=HASH,
        instrument_id="NSE:INFY", side=SignalSide.BUY,
        as_of=DECISION - timedelta(seconds=1), decision_at=DECISION,
        expires_at=DECISION + timedelta(minutes=5), evidence_tier=EvidenceTier.REPLAY,
        source_hash=HASH,
    )


def plan():
    return TradePlan(
        instrument_id="NSE:INFY", side="BUY", price_basis=PriceBasis.UNDERLYING,
        entry_condition=EntryCondition.LIMIT_ZONE, entry_low=Decimal("100"),
        entry_high=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("120"),
        invalidation="structure invalidated", max_holding_sessions=3,
        tick_size=Decimal("0.05"), lot_size=Decimal(1), unit_precision=0,
        quantity=Decimal(10), entry_expires_at=DECISION + timedelta(minutes=5),
        exit_triggers=(ExitTrigger.HARD_STOP, ExitTrigger.TAKE_PROFIT, ExitTrigger.TIME_STOP),
        paper_execution_eligible=True,
    )


def policy():
    return ExitPolicy(
        strategy_id="momentum", strategy_version="v1", policy_version="p1",
        rules=(
            ExitRule("stop", ExitTrigger.HARD_STOP, ExitRuleAction.FULL_EXIT,
                     quantity_fraction=Decimal(1), trigger_price=Decimal("90")),
            ExitRule("target", ExitTrigger.TAKE_PROFIT, ExitRuleAction.SCALE_OUT,
                     quantity_fraction=Decimal("0.5"), trigger_price=Decimal("120")),
            ExitRule("time", ExitTrigger.TIME_STOP, ExitRuleAction.TIME_EXIT,
                     quantity_fraction=Decimal(1), after_sessions=3),
        ),
    )


def bar(day, *, open_, high, low, close, minute=0):
    at = datetime(2026, 11, day, 10, minute, tzinfo=timezone.utc)
    return ExitReplayBar(at, Decimal(str(open_)), Decimal(str(high)),
                         Decimal(str(low)), Decimal(str(close)))


def sample(sample_id, bars, *, instrument="NSE:INFY", completed_day=20):
    return ExitPathSample(
        sample_id=sample_id, instrument_id=instrument,
        entry_at=datetime(2026, 11, 18, 10, tzinfo=timezone.utc),
        entry_price=Decimal("100"),
        completed_at=datetime(2026, 11, completed_day, 15, tzinfo=timezone.utc),
        bars=tuple(bars), source_sha256=HASH,
    )


def scenarios():
    return (
        sample("target-then-gap-stop", [
            bar(19, open_=100, high=121, low=99, close=120, minute=1),
            bar(20, open_=85, high=88, low=84, close=86, minute=1),
            bar(20, open_=86, high=88, low=85, close=87, minute=2),
        ]),
        sample("ambiguous-stop-first", [
            bar(19, open_=100, high=121, low=89, close=110, minute=1),
            bar(20, open_=110, high=115, low=105, close=112, minute=1),
            bar(20, open_=112, high=115, low=110, close=113, minute=2),
        ]),
        sample("time-exit", [
            bar(19, open_=100, high=105, low=99, close=101, minute=1),
            bar(20, open_=101, high=104, low=100, close=102, minute=1),
            bar(20, open_=102, high=104, low=101, close=103, minute=2),
        ]),
    )


def test_exit_probabilities_use_prior_matured_paths_and_model_partial_gap_risk():
    result = estimate_exit_probabilities(signal=signal(), plan=plan(), policy=policy(),
                                         samples=scenarios())
    assert result.sample_count == 3
    assert result.stop_exit_probability == Decimal(2) / Decimal(3)
    assert result.target_exit_probability == Decimal(1) / Decimal(3)
    assert result.time_exit_probability == Decimal(1) / Decimal(3)
    assert result.gap_stop_probability == Decimal(1) / Decimal(3)
    assert result.ambiguous_bar_count == 1
    assert result.outcomes[0].target_was_executed and result.outcomes[0].gap_stop
    assert result.outcomes[1].final_exit == "STOP"
    assert result.outcomes[1].target_was_executed is False
    assert result.outcomes[0].gap_loss_inr == Decimal("25")
    assert result.stop_exit_ci95 is None  # small n is not dressed up as calibrated
    assert "NOT_CALIBRATED" in result.probability_label


def test_exit_estimator_rejects_future_or_unmatured_paths_and_wrong_instrument():
    later = sample("future", scenarios()[2].bars, completed_day=24)
    with pytest.raises(ExitScenarioError, match="future or not-yet-matured"):
        estimate_exit_probabilities(signal=signal(), plan=plan(), policy=policy(), samples=[later])
    foreign = sample("foreign", scenarios()[2].bars, instrument="NSE:TCS")
    with pytest.raises(ExitScenarioError, match="instrument differs"):
        estimate_exit_probabilities(signal=signal(), plan=plan(), policy=policy(), samples=[foreign])


def test_exit_estimator_emits_wilson_intervals_only_after_30_prior_paths():
    paths = [sample(f"p-{i}", scenarios()[2].bars) for i in range(30)]
    result = estimate_exit_probabilities(signal=signal(), plan=plan(), policy=policy(), samples=paths)
    assert result.time_exit_ci95 is not None
    assert result.time_exit_probability == Decimal(1)
    assert result.confidence_interval_method == "WILSON_95_ONLY_WHEN_N_GE_30"


def test_short_gap_through_stop_uses_adverse_open_and_records_gap_loss():
    short_signal = replace(
        signal(), signal_id="e043444d-c290-4fb4-bf0a-d6941fd168a4", side=SignalSide.SELL_SHORT)
    short_plan = replace(plan(), side="SELL_SHORT", stop_price=Decimal("110"),
                         target_price=Decimal("80"))
    short_policy = ExitPolicy(
        strategy_id="momentum", strategy_version="v1", policy_version="short-p1",
        rules=(
            ExitRule("stop", ExitTrigger.HARD_STOP, ExitRuleAction.FULL_EXIT,
                     quantity_fraction=Decimal(1), trigger_price=Decimal("110")),
            ExitRule("target", ExitTrigger.TAKE_PROFIT, ExitRuleAction.FULL_EXIT,
                     quantity_fraction=Decimal(1), trigger_price=Decimal("80")),
            ExitRule("time", ExitTrigger.TIME_STOP, ExitRuleAction.TIME_EXIT,
                     quantity_fraction=Decimal(1), after_sessions=3),
        ),
    )
    bars = [bar(19, open_=120, high=122, low=119, close=121, minute=1),
            bar(20, open_=121, high=123, low=119, close=122, minute=1),
            bar(20, open_=122, high=124, low=120, close=123, minute=2)]
    result = estimate_exit_probabilities(
        signal=short_signal, plan=short_plan, policy=short_policy,
        samples=[sample("short-gap", bars)],
    )
    assert result.outcomes[0].final_exit == "GAP_STOP"
    assert result.outcomes[0].gross_pnl_inr == Decimal("-200")
    assert result.outcomes[0].gap_loss_inr == Decimal("100")


def test_exit_variant_benchmark_costs_every_variant_and_adjusts_family():
    baseline = policy()
    changed = ExitPolicy(
        strategy_id="momentum", strategy_version="v1", policy_version="p2",
        rules=(
            ExitRule("stop", ExitTrigger.HARD_STOP, ExitRuleAction.FULL_EXIT,
                     quantity_fraction=Decimal(1), trigger_price=Decimal("90")),
            ExitRule("target", ExitTrigger.TAKE_PROFIT, ExitRuleAction.FULL_EXIT,
                     quantity_fraction=Decimal(1), trigger_price=Decimal("120")),
            ExitRule("time", ExitTrigger.TIME_STOP, ExitRuleAction.TIME_EXIT,
                     quantity_fraction=Decimal(1), after_sessions=3),
        ),
    )
    costs = ExitCostSchedule(Decimal("5"), Decimal("0.25"), "kite-schedule-v1", HASH)
    result = compare_exit_variants(signal=signal(), plan=plan(), baseline=baseline,
                                   variants={"full-target": changed}, samples=scenarios(), costs=costs)
    assert result.sample_count == 3
    assert result.comparison_count == 1
    assert result.variant_mean_net_pnl_inr["baseline"] == Decimal("-22.50")
    assert result.variant_mean_net_pnl_inr["full-target"] == Decimal("35.83333333333333333333333333")
    assert result.paired_mean_delta_vs_baseline_inr["full-target"] == Decimal("58.33333333333333333333333333")
    assert result.holm_adjusted_p_value["full-target"] == result.paired_p_value["full-target"]
    # These are prior historical replay paths; a caller flag cannot turn them into forward evidence.
    assert result.prospective_evidence is False
    assert result.evidence_label == "HISTORICAL_REPLAY_NOT_PROSPECTIVE"


def test_exit_variant_benchmark_rejects_unbounded_family_and_bad_cost_provenance():
    with pytest.raises(ExitScenarioError, match="comparison limit"):
        compare_exit_variants(signal=signal(), plan=plan(), baseline=policy(),
                              variants={"a": policy(), "b": policy()}, samples=scenarios(),
                              costs=ExitCostSchedule(Decimal(0), Decimal(0), "cost", HASH),
                              max_comparisons=1)
    with pytest.raises(ExitScenarioError, match="source SHA-256"):
        ExitCostSchedule(Decimal(0), Decimal(0), "cost", "bad")
