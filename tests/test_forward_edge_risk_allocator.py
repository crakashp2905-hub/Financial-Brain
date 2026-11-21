import pytest

from financial_brain.forward_edge.risk_allocator import (
    CandidateRisk,
    RiskAllocationPolicy,
    RiskAllocatorError,
    allocate_with_risk_controls,
)


def _candidates():
    return (CandidateRisk("a", "NSE:A", "IT", 0.02, 0.22, 0.09),
            CandidateRisk("b", "NSE:B", "IT", 0.018, 0.20, 0.025),
            CandidateRisk("c", "NSE:C", "BANK", 0.015, 0.14, 0.02))


def _policy(concentration=0.2, cvar=0.5):
    return RiskAllocationPolicy(0.9, 0.5, {"IT": 0.55, "BANK": 0.55},
                                0.14, 0.5, cvar, concentration, step_size=0.02, iterations=1000)


def test_allocation_is_bounded_by_cash_name_sector_and_volatility_limits() -> None:
    corr = ((1.0, 0.6, 0.1), (0.6, 1.0, 0.05), (0.1, 0.05, 1.0))
    result = allocate_with_risk_controls(_candidates(), corr, _policy())
    assert sum(result.weights.values()) <= 0.9 + 1e-10
    assert result.cash_weight >= 0.1 - 1e-10
    assert result.weights["a"] + result.weights["b"] <= 0.55 + 1e-10
    assert max(result.weights.values()) <= 0.5 + 1e-10
    assert result.forecast_portfolio_volatility <= 0.14 + 1e-10
    assert result.weighted_cvar_loss > 0
    assert not result.promotion_allowed


def test_cvar_and_concentration_penalties_change_allocation_deterministically() -> None:
    corr = ((1.0, 0.2, 0.0), (0.2, 1.0, 0.0), (0.0, 0.0, 1.0))
    ordinary = allocate_with_risk_controls(_candidates(), corr, _policy(concentration=0, cvar=0))
    risk_controlled = allocate_with_risk_controls(_candidates(), corr, _policy(concentration=0.2, cvar=0.5))
    assert risk_controlled == allocate_with_risk_controls(_candidates(), corr, _policy(concentration=0.2, cvar=0.5))
    assert risk_controlled.weights["a"] < ordinary.weights["a"]
    ordinary_it_share = ((ordinary.weights["a"] + ordinary.weights["b"])
                         / sum(ordinary.weights.values()))
    controlled_it_share = ((risk_controlled.weights["a"] + risk_controlled.weights["b"])
                           / sum(risk_controlled.weights.values()))
    assert controlled_it_share < ordinary_it_share


def test_unknown_sector_or_invalid_correlation_cannot_be_allocated() -> None:
    with pytest.raises(RiskAllocatorError, match="sector needs"):
        allocate_with_risk_controls(_candidates(), ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    RiskAllocationPolicy(1, 0.5, {"IT": 1}, 0.2, 1, 1, 1))
    with pytest.raises(RiskAllocatorError, match="positive semidefinite"):
        allocate_with_risk_controls(_candidates(), ((1, 1, 1), (1, 1, -1), (1, -1, 1)), _policy())
