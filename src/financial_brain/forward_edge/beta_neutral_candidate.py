"""A separately registered beta-neutral exploratory alternative, never hidden preprocessing."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Mapping

import numpy as np

from .registry import MechanismHypothesis, Status, StrategySpec, parameter_sha256


class BetaNeutralError(ValueError):
    """The cross-sectional beta-neutral candidate cannot be evaluated safely."""


def beta_neutral_scores(expected_net_alpha: Mapping[str, float], betas: Mapping[str, float]) -> dict[str, float]:
    """Return net-alpha residuals after an intercept and market-beta cross-sectional fit.

    This is a distinct candidate signal transformation. It is not applied implicitly to the
    original strategy, and its output remains research-only until separately evaluated.
    """
    names = tuple(sorted(expected_net_alpha))
    if len(names) < 3 or set(names) != set(betas):
        raise BetaNeutralError("at least three names with matched beta exposures are required")
    alpha = np.asarray([expected_net_alpha[name] for name in names], dtype=float)
    beta = np.asarray([betas[name] for name in names], dtype=float)
    if not np.isfinite(alpha).all() or not np.isfinite(beta).all():
        raise BetaNeutralError("alpha and beta inputs must be finite")
    design = np.column_stack((np.ones(len(names)), beta))
    if np.linalg.matrix_rank(design) < 2:
        raise BetaNeutralError("cross-sectional beta exposures have no variation")
    coefficients, *_ = np.linalg.lstsq(design, alpha, rcond=None)
    residuals = alpha - design @ coefficients
    return {name: float(residuals[index]) for index, name in enumerate(names)}


def beta_neutral_strategy_spec() -> StrategySpec:
    """Build an independent exploratory registry entry with this candidate's own code digest."""
    module_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    params = {"regression": "cross_sectional_ols_with_intercept", "factor": "market_beta",
              "signal": "after_cost_net_alpha_residual"}
    hypothesis = MechanismHypothesis(
        "A beta-neutral relative signal may isolate security selection from broad market exposure.",
        "Cross-sectional residual mispricing may persist when investors price market and security risks differently.",
        "Unhedged benchmark exposure or the opposing side of the relative trade may bear the risk premium.",
        ("stable beta estimates", "broad cross-sectional dispersion", "liquid names"),
        "After-cost residual returns do not improve on the matched original and beta-matched control.",
    )
    return StrategySpec(
        strategy_id="PE66-BETA-NEUTRAL-CS",
        family="beta_neutral_cross_sectional_candidate", version=1, code_sha256=module_sha,
        asset_scope=("NSE_EQ",), decision_clock="daily_close", direction="LONG_SHORT",
        exit_rule="time_20", horizon_sessions=20,
        data_requirements=("after_cost_net_alpha", "point_in_time_market_beta"),
        source_lineage="src/financial_brain/forward_edge/beta_neutral_candidate.py::beta_neutral_scores",
        status=Status.EXPLORATORY, mechanism_hypothesis=hypothesis,
        parameter_sha256=parameter_sha256(params),
    )
