import numpy as np
import pytest

from financial_brain.forward_edge.beta_neutral_candidate import (
    BetaNeutralError,
    beta_neutral_scores,
    beta_neutral_strategy_spec,
)
from financial_brain.forward_edge.registry import Registry, Status


def test_beta_neutral_candidate_removes_linear_market_beta_exposure():
    betas = {"A": 0.5, "B": 0.8, "C": 1.0, "D": 1.3, "E": 1.8}
    alpha = {name: 0.01 + 0.02 * beta + residual
             for (name, beta), residual in zip(betas.items(), (0.01, -0.01, 0.02, -0.02, 0.0))}
    residual = beta_neutral_scores(alpha, betas)
    exposures = np.asarray([betas[name] for name in sorted(betas)])
    values = np.asarray([residual[name] for name in sorted(residual)])
    assert abs(float(values.sum())) < 1e-12
    assert abs(float(values @ exposures)) < 1e-12


def test_beta_neutral_candidate_requires_matched_finite_varied_inputs():
    with pytest.raises(BetaNeutralError, match="at least three"):
        beta_neutral_scores({"A": 0.1, "B": 0.2}, {"A": 1.0, "B": 2.0})
    with pytest.raises(BetaNeutralError, match="no variation"):
        beta_neutral_scores({"A": 0.1, "B": 0.2, "C": 0.3}, {"A": 1.0, "B": 1.0, "C": 1.0})
    with pytest.raises(BetaNeutralError, match="finite"):
        beta_neutral_scores({"A": 0.1, "B": float("nan"), "C": 0.3},
                            {"A": 1.0, "B": 2.0, "C": 3.0})


def test_beta_neutral_alternative_has_separate_code_identity_and_remains_exploratory():
    spec = beta_neutral_strategy_spec()
    assert spec.status is Status.EXPLORATORY
    assert spec.strategy_id == "PE66-BETA-NEUTRAL-CS"
    assert spec.code_sha256 != "b" * 64
    registry = Registry()
    registered = registry.register(spec)
    assert registered.code_sha256 == spec.code_sha256
    assert registered.status is Status.EXPLORATORY
