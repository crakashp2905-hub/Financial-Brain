"""Small distributional-return and empirical tail-risk research primitives.

These functions provide deterministic contracts for offline research. They do not
train a critic, establish episode independence, or authorize a trading policy.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence


class DistributionalRiskError(ValueError):
    """Invalid distributional-return or tail-risk input."""


def quantile_bellman_targets(
    reward: float,
    bootstrap_quantiles: Sequence[float],
    *,
    gamma: float,
    terminal: bool = False,
    bounds: tuple[float, float] | None = None,
) -> tuple[float, ...]:
    """Return sorted clipped distributional Bellman targets for one transition."""
    if not math.isfinite(reward) or not math.isfinite(gamma) or not 0 <= gamma <= 1:
        raise DistributionalRiskError("reward must be finite and gamma in [0, 1]")
    if terminal:
        if bootstrap_quantiles:
            raise DistributionalRiskError("terminal targets cannot include bootstrap quantiles")
        values: tuple[float, ...] = (reward,)
    else:
        if not bootstrap_quantiles or any(not math.isfinite(x) for x in bootstrap_quantiles):
            raise DistributionalRiskError("nonterminal targets need finite bootstrap quantiles")
        values = tuple(sorted(reward + gamma * x for x in bootstrap_quantiles))
    if bounds is not None:
        low, high = bounds
        if not math.isfinite(low) or not math.isfinite(high) or low > high:
            raise DistributionalRiskError("bounds must be finite and ordered")
        values = tuple(min(high, max(low, x)) for x in values)
    return values


def quantile_huber_loss(
    predicted: Sequence[float], target: Sequence[float], *,
    quantile_levels: Sequence[float], huber_kappa: float = 1.0,
) -> float:
    """Mean pairwise quantile-Huber loss with explicit quantile levels."""
    if not predicted or not target or len(predicted) != len(quantile_levels):
        raise DistributionalRiskError("predictions, targets and quantile levels must be non-empty and aligned")
    if any(not math.isfinite(x) for x in (*predicted, *target, *quantile_levels)):
        raise DistributionalRiskError("quantile inputs must be finite")
    if any(not 0 < tau < 1 for tau in quantile_levels):
        raise DistributionalRiskError("quantile levels must be in (0, 1)")
    if not math.isfinite(huber_kappa) or huber_kappa <= 0:
        raise DistributionalRiskError("Huber kappa must be positive and finite")
    losses: list[float] = []
    for tau, estimate in zip(quantile_levels, predicted):
        for observed in target:
            error = observed - estimate
            absolute = abs(error)
            huber = 0.5 * error * error if absolute <= huber_kappa else huber_kappa * (absolute - 0.5 * huber_kappa)
            losses.append(abs(tau - (1.0 if error < 0 else 0.0)) * huber / huber_kappa)
    return sum(losses) / len(losses)


@dataclass(frozen=True, slots=True)
class TailRiskEstimate:
    """Episode-bootstrap CVaR estimate; positive values denote loss."""

    var_loss: float
    cvar_loss: float
    lower_cvar_loss: float
    upper_cvar_loss: float
    episode_count: int
    tail_episode_count: int
    confidence: float
    method: str = "episode_iid_bootstrap"
    caveat: str = "Interval assumes exchangeable episodes; it does not correct regime dependence."


def _cvar_loss(episode_net_returns: Sequence[float], tail_probability: float) -> tuple[float, float, int]:
    ordered_losses = sorted((-value for value in episode_net_returns), reverse=True)
    count = max(1, math.ceil(tail_probability * len(ordered_losses)))
    return ordered_losses[count - 1], sum(ordered_losses[:count]) / count, count


def episode_cvar_with_uncertainty(
    episode_net_returns: Sequence[float], *, tail_probability: float = 0.05,
    confidence: float = 0.95, bootstrap_samples: int = 2000, seed: int = 0,
) -> TailRiskEstimate:
    """Estimate loss VaR/CVaR and an episode-resampling percentile interval.

    Input values must be realized *net* portfolio returns per completed episode.
    At least 20 episodes and 2 tail episodes are required to avoid false precision.
    """
    if any(not math.isfinite(x) for x in episode_net_returns):
        raise DistributionalRiskError("episode net returns must be finite")
    if not 0 < tail_probability < 0.5 or not 0 < confidence < 1:
        raise DistributionalRiskError("tail probability and confidence must be in valid open intervals")
    if bootstrap_samples < 200 or seed < 0:
        raise DistributionalRiskError("at least 200 bootstrap samples and a non-negative seed are required")
    n = len(episode_net_returns)
    tail_n = math.ceil(tail_probability * n)
    if n < 20 or tail_n < 2:
        raise DistributionalRiskError("at least 20 episodes and 2 empirical tail episodes are required")
    var_loss, cvar_loss, _ = _cvar_loss(episode_net_returns, tail_probability)
    rng = random.Random(seed)
    bootstrap_cvar = []
    for _ in range(bootstrap_samples):
        sample = [episode_net_returns[rng.randrange(n)] for _ in range(n)]
        bootstrap_cvar.append(_cvar_loss(sample, tail_probability)[1])
    bootstrap_cvar.sort()
    tail = (1.0 - confidence) / 2.0
    low_index = min(bootstrap_samples - 1, math.floor(tail * bootstrap_samples))
    high_index = min(bootstrap_samples - 1, math.ceil((1.0 - tail) * bootstrap_samples) - 1)
    return TailRiskEstimate(var_loss, cvar_loss, bootstrap_cvar[low_index], bootstrap_cvar[high_index],
                            n, tail_n, confidence)


__all__ = ["DistributionalRiskError", "TailRiskEstimate", "episode_cvar_with_uncertainty",
           "quantile_bellman_targets", "quantile_huber_loss"]
