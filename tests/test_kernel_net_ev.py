from datetime import date, datetime, timedelta, timezone
from dataclasses import replace
from decimal import Decimal

import pytest

from financial_brain.costs.india import CostModel, Segment
from financial_brain.forward_edge.cost_schedule import CostSchedule
from financial_brain.kernel.net_ev import (
    FillScenario, NetEVCalibration, NetEVError, NetEVOutcome, net_ev,
)
from financial_brain.kernel.trader_signal import (
    DeskId, EvidenceTier, SignalSide, TraderSignal,
)


DECISION = datetime(2026, 11, 23, 18, 30, tzinfo=timezone.utc)  # 24 Nov in India
HASH = "a" * 64


def signal():
    return TraderSignal(
        signal_id="4dbe4c48-7104-4f47-b84c-14a55cd49f24", desk_id=DeskId.SWING,
        strategy_id="momentum", strategy_version="v1", strategy_hash=HASH,
        instrument_id="NSE:INFY", side=SignalSide.BUY,
        as_of=DECISION - timedelta(seconds=1), decision_at=DECISION,
        expires_at=DECISION + timedelta(minutes=5), evidence_tier=EvidenceTier.REPLAY,
        source_hash=HASH,
    )


def schedule():
    return CostSchedule(
        schedule_id="india-paper", version="v1", effective_from=date(2026, 11, 24),
        effective_to=None, segment=Segment.DELIVERY, model=CostModel(),
        source_ref="tests/fixture-cost-schedule", source_sha256=HASH,
    )


def scenario(*, stale=False, calibrated=False):
    outcomes = (
        NetEVOutcome(
            name="filled-target", probability=Decimal("0.5"), gross_pnl_inr=Decimal("100"),
            entry_turnover_inr=Decimal("10000"), exit_turnover_inr=Decimal("10200"),
            spread_cost_inr=Decimal("5"), filled_quantity=Decimal("10"),
            visible_quantity=Decimal("25"), liquidity_bucket="large",
            historical_gross_pnl_samples_inr=(tuple(Decimal(100 + i) for i in range(35))
                                              if calibrated else ()),
        ),
        NetEVOutcome(
            name="not-filled", probability=Decimal("0.5"), gross_pnl_inr=Decimal(0),
            entry_turnover_inr=Decimal(0), exit_turnover_inr=Decimal(0),
            spread_cost_inr=Decimal(0), filled_quantity=Decimal(0), visible_quantity=Decimal(0),
            liquidity_bucket="large",
            historical_gross_pnl_samples_inr=(tuple(Decimal(0) for _ in range(35))
                                              if calibrated else ()),
        ),
    )
    event_at = DECISION - timedelta(seconds=2 if stale else 0.1)
    available_at = event_at + timedelta(milliseconds=20)
    return FillScenario(
        outcomes=outcomes, instrument_id="NSE:INFY", bid=Decimal("100"), ask=Decimal("100.1"),
        quote_event_at=event_at, quote_available_at=available_at,
        max_quote_age_ms=500, quote_source_id="KITE_CONNECT_READ_ONLY",
        quote_source_sha256=HASH,
        calibration=(NetEVCalibration("holdout-v1", HASH, True) if calibrated else None),
    )


def test_net_ev_includes_no_fill_probability_and_versioned_indian_round_trip_costs():
    result = net_ev(signal(), scenario(), schedule())
    assert result.expected_gross_pnl_inr == Decimal("50.0")
    assert result.expected_cost_inr > 0
    assert result.expected_net_pnl_inr == result.expected_gross_pnl_inr - result.expected_cost_inr
    assert result.net_pnl_distribution[1].net_pnl_inr == 0
    assert result.calibration_status == "UNCALIBRATED_SCENARIO"
    assert result.mean_confidence_interval_95_inr is None
    assert result.cost_schedule_sha256 == schedule().digest()
    assert result.evidence_tier == "REPLAY"
    assert result.is_execution_authorization is False


def test_net_ev_only_emits_calibration_interval_for_large_out_of_time_sample():
    result = net_ev(signal(), scenario(calibrated=True), schedule())
    assert result.mean_confidence_interval_95_inr is not None
    assert result.confidence_interval_method == \
        "NORMAL_APPROXIMATION_IID_95_CONDITIONAL_SCENARIO_PROBABILITIES"
    assert result.calibration_sample_count == 70
    assert result.calibration_id == "holdout-v1"
    assert result.calibration_status == "OUT_OF_TIME_CALIBRATED"


def test_positive_gross_edge_can_become_negative_after_costs():
    fill = scenario()
    low_gross = replace(fill.outcomes[0], gross_pnl_inr=Decimal("10"))
    result = net_ev(signal(), replace(fill, outcomes=(low_gross, fill.outcomes[1])), schedule())
    assert result.expected_gross_pnl_inr > 0
    assert result.expected_cost_inr > result.expected_gross_pnl_inr
    assert result.expected_net_pnl_inr < 0
    assert result.loss_distribution[0].net_pnl_inr < 0


def test_net_ev_rejects_stale_or_future_unavailable_quote():
    with pytest.raises(NetEVError, match="source quote exceeds"):
        net_ev(signal(), scenario(stale=True), schedule())


def test_net_ev_rejects_missing_cost_model_and_negative_liquidity():
    with pytest.raises(NetEVError, match="CostSchedule"):
        net_ev(signal(), scenario(), None)
    with pytest.raises(NetEVError, match="non-negative"):
        NetEVOutcome(name="bad", probability=Decimal(1), gross_pnl_inr=Decimal(1),
                     entry_turnover_inr=Decimal(1), exit_turnover_inr=Decimal(1),
                     spread_cost_inr=Decimal(0), filled_quantity=Decimal(1),
                     visible_quantity=Decimal(-1), liquidity_bucket="large")
