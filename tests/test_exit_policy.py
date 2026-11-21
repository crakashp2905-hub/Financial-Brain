from decimal import Decimal

import pytest

from financial_brain.kernel.exit_policy import ExitPolicy, ExitPolicyError, ExitRule, ExitRuleAction
from financial_brain.kernel.trader_signal import ExitTrigger


def _policy(rules=None, **changes):
    base = {
        "strategy_id": "momentum", "strategy_version": "3.1", "policy_version": "exit-v2",
        "rules": rules or (
            ExitRule("hard-stop", ExitTrigger.HARD_STOP, ExitRuleAction.FULL_EXIT,
                     quantity_fraction=Decimal("1"), trigger_price=Decimal("95")),
            ExitRule("tp-1", ExitTrigger.TAKE_PROFIT, ExitRuleAction.SCALE_OUT,
                     quantity_fraction=Decimal("0.25"), trigger_price=Decimal("110")),
            ExitRule("trail", ExitTrigger.TRAILING_STOP, ExitRuleAction.TRAIL_STOP,
                     trailing_distance=Decimal("4")),
            ExitRule("time", ExitTrigger.TIME_STOP, ExitRuleAction.TIME_EXIT,
                     quantity_fraction=Decimal("1"), after_sessions=8),
        ),
    }
    return ExitPolicy(**(base | changes))


def test_versioned_exit_policy_hash_is_stable_and_parameter_sensitive():
    policy = _policy()
    assert policy.policy_hash == _policy().policy_hash
    assert len(policy.policy_hash) == 64
    changed = _policy(rules=policy.rules[:-1] + (
        ExitRule("time", ExitTrigger.TIME_STOP, ExitRuleAction.TIME_EXIT,
                 quantity_fraction=Decimal("1"), after_sessions=9),
    ))
    assert changed.policy_hash != policy.policy_hash


def test_policy_supports_scale_out_breakeven_trailing_and_time_exit():
    policy = _policy(rules=(
        ExitRule("sl", ExitTrigger.HARD_STOP, ExitRuleAction.FULL_EXIT,
                 quantity_fraction=Decimal("1"), trigger_price=Decimal("95")),
        ExitRule("scale", ExitTrigger.TAKE_PROFIT, ExitRuleAction.SCALE_OUT,
                 quantity_fraction=Decimal("0.3"), trigger_price=Decimal("110")),
        ExitRule("be", ExitTrigger.TAKE_PROFIT, ExitRuleAction.MOVE_STOP_TO_BREAKEVEN),
        ExitRule("trail", ExitTrigger.TRAILING_STOP, ExitRuleAction.TRAIL_STOP,
                 trailing_distance=Decimal("2")),
        ExitRule("time", ExitTrigger.TIME_STOP, ExitRuleAction.TIME_EXIT,
                 quantity_fraction=Decimal("1"), after_sessions=5),
    ))
    assert len(policy.rules) == 5


@pytest.mark.parametrize("values", [
    {"rule_id": "bad-full", "trigger": ExitTrigger.HARD_STOP, "action": ExitRuleAction.FULL_EXIT,
     "quantity_fraction": Decimal("0.5")},
    {"rule_id": "bad-scale", "trigger": ExitTrigger.TAKE_PROFIT, "action": ExitRuleAction.SCALE_OUT,
     "quantity_fraction": Decimal("1")},
    {"rule_id": "bad-time", "trigger": ExitTrigger.TAKE_PROFIT, "action": ExitRuleAction.TIME_EXIT,
     "quantity_fraction": Decimal("1"), "after_sessions": 2},
])
def test_rule_invariants_fail_closed(values):
    with pytest.raises(ExitPolicyError):
        ExitRule(**values)


def test_policy_requires_stop_unique_rule_ids_and_valid_scale_out_total():
    with pytest.raises(ExitPolicyError, match="hard-stop"):
        _policy(rules=(ExitRule("tp", ExitTrigger.TAKE_PROFIT, ExitRuleAction.FULL_EXIT,
                                quantity_fraction=Decimal("1")),))
    with pytest.raises(ExitPolicyError, match="unique"):
        _policy(rules=(
            ExitRule("sl", ExitTrigger.HARD_STOP, ExitRuleAction.FULL_EXIT, quantity_fraction=Decimal("1")),
            ExitRule("sl", ExitTrigger.TAKE_PROFIT, ExitRuleAction.SCALE_OUT,
                     quantity_fraction=Decimal("0.2")),
        ))
    with pytest.raises(ExitPolicyError, match="cannot exceed"):
        _policy(rules=(
            ExitRule("sl", ExitTrigger.HARD_STOP, ExitRuleAction.FULL_EXIT, quantity_fraction=Decimal("1")),
            ExitRule("tp1", ExitTrigger.TAKE_PROFIT, ExitRuleAction.SCALE_OUT,
                     quantity_fraction=Decimal("0.6")),
            ExitRule("tp2", ExitTrigger.TAKE_PROFIT, ExitRuleAction.SCALE_OUT,
                     quantity_fraction=Decimal("0.5")),
        ))
