from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.strategy_economics_gate import (
    StrategyEconomicsGateError, StrategyEconomicsRule, evaluate_strategy_economics,
)
from financial_brain.kernel.trade_economics import TradingCosts, estimate_trade_economics
from financial_brain.kernel.trader_signal import EntryCondition, ExitTrigger, PriceBasis, TradePlan


def _plan():
    return TradePlan(
        instrument_id="NSE:INFY", side="BUY", price_basis=PriceBasis.UNDERLYING,
        entry_condition=EntryCondition.LIMIT_ZONE, entry_low=Decimal("100"),
        entry_high=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("120"),
        invalidation="support close", max_holding_sessions=5,
        tick_size=Decimal("0.05"), lot_size=Decimal("1"), unit_precision=0,
        quantity=Decimal("10"), entry_expires_at=datetime(2026, 11, 21, 11, tzinfo=timezone.utc),
        exit_triggers=(ExitTrigger.HARD_STOP, ExitTrigger.TAKE_PROFIT),
        paper_execution_eligible=True,
    )


def _rule(strategy: str, minimum_rr: str, *, minimum_net: str = "0"):
    return StrategyEconomicsRule(
        strategy_id=strategy, strategy_version="v1", minimum_net_reward_to_risk=Decimal(minimum_rr),
        minimum_expected_net_inr=Decimal(minimum_net), calibration_start=date(2020, 1, 1),
        calibration_end=date(2024, 1, 1), evidence_sha256="c" * 64, rule_version="r1",
    )


def test_strategy_specific_thresholds_can_differ_without_universal_two_to_one():
    economics = estimate_trade_economics(_plan(), TradingCosts(0, 0, 0, 0))
    flexible = evaluate_strategy_economics(
        _rule("swing", "1.5"), economics, strategy_id="swing", strategy_version="v1",
        expected_net_inr=Decimal("50"),
    )
    strict = evaluate_strategy_economics(
        _rule("intraday", "2.5"), economics, strategy_id="intraday", strategy_version="v1",
        expected_net_inr=Decimal("50"),
    )
    assert flexible.eligible_for_research
    assert not strict.eligible_for_research
    assert strict.reasons == ("NET_REWARD_RISK_BELOW_STRATEGY_THRESHOLD",)


def test_costs_and_net_expectancy_can_fail_separate_strategy_gates():
    economics = estimate_trade_economics(
        _plan(), TradingCosts(Decimal("50"), Decimal("30"), Decimal("20"), Decimal("10")),
    )
    result = evaluate_strategy_economics(
        _rule("swing", "1", minimum_net="20"), economics,
        strategy_id="swing", strategy_version="v1", expected_net_inr=Decimal("19"),
    )
    assert not result.eligible_for_research
    assert set(result.reasons) == {
        "NET_REWARD_RISK_BELOW_STRATEGY_THRESHOLD", "EXPECTED_NET_BELOW_STRATEGY_THRESHOLD",
    }
    assert result.forecast_is_uncertain


def test_rule_requires_version_identity_and_evidence_hash():
    rule = _rule("swing", "1")
    economics = estimate_trade_economics(_plan(), TradingCosts(0, 0, 0, 0))
    with pytest.raises(StrategyEconomicsGateError, match="identity/version"):
        evaluate_strategy_economics(
            rule, economics, strategy_id="swing", strategy_version="v2", expected_net_inr=10,
        )
    with pytest.raises(StrategyEconomicsGateError, match="evidence_sha256"):
        StrategyEconomicsRule(
            "swing", "v1", Decimal("1"), Decimal(0), date(2020, 1, 1), date(2024, 1, 1),
            "bad", "r1",
        )
