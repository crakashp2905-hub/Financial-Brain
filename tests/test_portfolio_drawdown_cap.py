import pytest

from financial_brain.portfolio.drawdown_cap import (
    DrawdownCapError,
    DrawdownDeleveragingPolicy,
    cap_risk_for_drawdown,
)

POLICY = DrawdownDeleveragingPolicy(reduce_at=0.05, halt_at=0.20, maximum_risk_fraction=0.8)


def test_risk_cap_reduces_monotonically_and_halts_at_hard_drawdown_limit():
    values = [cap_risk_for_drawdown(equity_path=path, proposed_risk_fraction=0.8,
                                    policy=POLICY).allowed_risk_fraction
              for path in ((100.0, 100.0), (100.0, 95.0), (100.0, 90.0), (100.0, 80.0))]
    assert values == [0.8, 0.8, pytest.approx(0.5333333333333333), 0.0]
    assert all(left >= right for left, right in zip(values, values[1:]))


def test_recovery_does_not_silently_restore_risk_after_a_deep_loss():
    result = cap_risk_for_drawdown(equity_path=(100.0, 75.0, 95.0),
                                  proposed_risk_fraction=0.9, policy=POLICY)
    assert result.action == "HALT"
    assert result.current_equity == 95.0
    assert result.maximum_observed_drawdown == pytest.approx(0.25)
    assert result.allowed_risk_fraction == 0.0


def test_allocator_cannot_double_risk_after_loss_and_replays_are_deterministic():
    after_small_loss = cap_risk_for_drawdown(equity_path=(100.0, 92.0),
                                             proposed_risk_fraction=0.9, policy=POLICY)
    after_larger_loss = cap_risk_for_drawdown(equity_path=(100.0, 92.0, 85.0),
                                              proposed_risk_fraction=0.9, policy=POLICY)
    assert after_larger_loss.allowed_risk_fraction < after_small_loss.allowed_risk_fraction
    assert after_larger_loss.allowed_risk_fraction <= after_larger_loss.proposed_risk_fraction
    assert after_larger_loss == cap_risk_for_drawdown(
        equity_path=(100.0, 92.0, 85.0), proposed_risk_fraction=0.9, policy=POLICY)
    assert after_larger_loss.report_sha256 != after_small_loss.report_sha256


def test_drawdown_policy_and_equity_path_must_be_valid():
    with pytest.raises(DrawdownCapError, match="reduce_at"):
        DrawdownDeleveragingPolicy(0.2, 0.1)
    with pytest.raises(DrawdownCapError, match="at least two"):
        cap_risk_for_drawdown(equity_path=(100.0,), proposed_risk_fraction=0.2, policy=POLICY)
    with pytest.raises(DrawdownCapError, match="finite and positive"):
        cap_risk_for_drawdown(equity_path=(100.0, 0.0), proposed_risk_fraction=0.2, policy=POLICY)
