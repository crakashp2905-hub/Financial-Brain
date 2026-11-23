from decimal import Decimal

import pytest

from financial_brain.signals.plan import (Comparison, EntryCondition, InstrumentPrecision,
                                          PlanError, SignalPlan)


def precision():
    return InstrumentPrecision(Decimal("0.05"), Decimal("1"), Decimal("1"))


def test_plan_keeps_entry_exit_horizon_and_exchange_precision():
    plan = SignalPlan("s-1", (EntryCondition("close_vs_breakout", Comparison.GT, 0),),
                      5, precision(), -0.04, 0.08,
                      (EntryCondition("thesis_invalid", Comparison.EQ, 1),), True)
    assert plan.precision.quantize_quantity(Decimal("12.9")) == Decimal("12")
    assert plan.precision.quantize_price(Decimal("100.03")) == Decimal("100.05")
    assert plan.holding_window_sessions == 5


def test_missing_entry_or_invalidation_reason_is_rejected():
    with pytest.raises(PlanError, match="entry conditions"):
        SignalPlan("s", (), 1, precision(), None, None, (), True)
    with pytest.raises(PlanError, match="explicit reason"):
        SignalPlan("s", (), 1, precision(), None, None, (), False)
