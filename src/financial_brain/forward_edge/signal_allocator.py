"""Convert common signal/net-alpha observations to one research-only target portfolio (PE-0060)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Mapping

import numpy as np

from financial_brain.decisions.net_alpha import NetAlpha
from financial_brain.forward_edge.forecast_hurdle import FrozenForecastHurdle
from .target_portfolio import TargetPortfolio, plan_target_portfolio

SIGNAL_ACTIONS = frozenset({"BUY", "HOLD", "SELL", "NO_SIGNAL"})


class SignalAllocatorError(ValueError):
    pass


@dataclass(frozen=True)
class ForecastObservation:
    """Immutable decision-time record, including forecasts rejected from paper entry."""

    instrument: str
    action: str
    reason: str
    forecast_net_alpha: float
    horizon: int
    source: str
    calibrated: bool
    calibration_id: str
    estimated_costs: float
    cost_to_benefit_ratio: float | None


@dataclass(frozen=True)
class SignalTargetResult:
    target: TargetPortfolio
    dispositions: tuple[tuple[str, str, float], ...]
    counterfactuals: tuple[ForecastObservation, ...]
    research_only: bool = True


def allocate_signals(*, as_of: date, scheduled_rebalances: frozenset[date],
                     signals: Mapping[str, tuple[str, NetAlpha]], covariance: np.ndarray,
                     covariance_names: tuple[str, ...], previous_weights: Mapping[str, float],
                     min_forecast_net_alpha: float | None = None,
                     frozen_hurdle: FrozenForecastHurdle | None = None,
                     hurdle_policy_sha256: str | None = None,
                     max_name_weight: float = 0.10,
                     max_forecast_cost_to_benefit_ratio: float = 1.0,
                     cash_floor: float = 0.0, max_one_way_turnover: float = 0.20,
                     turnover_cost_per_unit: float = 0.0, risk_aversion: float = 1.0,
                     iterations: int = 500) -> SignalTargetResult:
    """Route every named signal through one allocator; input NetAlpha already itemizes trade costs.

    The threshold is a per-horizon expected-return hurdle, not risk-adjusted utility. SELL signals
    receive a negative target score; HOLD/NO_SIGNAL remain non-entry observations. Every held name
    must be represented so the allocator cannot silently orphan an existing position.
    """
    effective_hurdle: float
    if frozen_hurdle is not None:
        if min_forecast_net_alpha is not None or hurdle_policy_sha256 is None:
            raise SignalAllocatorError("frozen hurdle requires its strategy pin and no mutable threshold")
        try:
            frozen_hurdle.validate_for(as_of=as_of, expected_sha256=hurdle_policy_sha256)
        except ValueError as exc:
            raise SignalAllocatorError(str(exc)) from exc
        effective_hurdle = frozen_hurdle.min_net_alpha
    else:
        if min_forecast_net_alpha is None or not isfinite(min_forecast_net_alpha) or min_forecast_net_alpha < 0:
            raise SignalAllocatorError("minimum forecast net-alpha hurdle must be finite and non-negative")
        if hurdle_policy_sha256 is not None:
            raise SignalAllocatorError("a strategy hurdle pin requires a frozen hurdle policy")
        effective_hurdle = min_forecast_net_alpha
    if not isfinite(max_forecast_cost_to_benefit_ratio) or max_forecast_cost_to_benefit_ratio < 0:
        raise SignalAllocatorError("maximum forecast cost-to-benefit ratio must be finite and non-negative")
    names = tuple(sorted(signals))
    if not names or set(names) != set(covariance_names) or len(set(covariance_names)) != len(covariance_names):
        raise SignalAllocatorError("signals and covariance universe must contain the same unique names")
    held = set(previous_weights) - {"CASH"}
    if not held.issubset(signals):
        raise SignalAllocatorError("every held instrument needs an explicit signal observation")
    horizons = set()
    alpha: dict[str, float] = {}
    dispositions = []
    observations = []
    for name in names:
        action, forecast = signals[name]
        if action not in SIGNAL_ACTIONS or not isinstance(forecast, NetAlpha):
            raise SignalAllocatorError(f"{name}: expected a supported action and NetAlpha forecast")
        horizons.add(forecast.horizon)
        if action == "SELL":
            score, reason = -max(abs(forecast.net), effective_hurdle, 1e-12), "SELL_SIGNAL"
        elif action == "NO_SIGNAL":
            score, reason = 0.0, "NO_SIGNAL"
        elif not forecast.calibrated:
            score, reason = 0.0, "NO_TRADE_UNCALIBRATED_FORECAST"
        elif forecast.net <= effective_hurdle:
            score, reason = 0.0, "NO_TRADE_NET_ALPHA_HURDLE"
        elif forecast.excess <= 0 or forecast.costs / forecast.excess > max_forecast_cost_to_benefit_ratio:
            score, reason = 0.0, "NO_TRADE_COST_BENEFIT_RATIO"
        else:
            score, reason = forecast.net, "NET_ALPHA_ABOVE_HURDLE"
        alpha[name] = score
        dispositions.append((name, reason, forecast.net))
        observations.append(ForecastObservation(
            instrument=name, action=action, reason=reason, forecast_net_alpha=forecast.net,
            horizon=forecast.horizon, source=forecast.source, calibrated=forecast.calibrated,
            calibration_id=forecast.calibration_id, estimated_costs=forecast.costs,
            cost_to_benefit_ratio=(forecast.costs / forecast.excess if forecast.excess > 0 else None),
        ))
    if len(horizons) != 1:
        raise SignalAllocatorError("all forecasts in one target book must share the same holding horizon")
    target = plan_target_portfolio(
        as_of=as_of, scheduled_rebalances=scheduled_rebalances, expected_net_alpha=alpha,
        covariance=covariance, covariance_names=tuple(sorted(covariance_names)),
        previous_weights=previous_weights, max_name_weight=max_name_weight,
        cash_floor=cash_floor, max_one_way_turnover=max_one_way_turnover,
        turnover_cost_per_unit=turnover_cost_per_unit, risk_aversion=risk_aversion,
        iterations=iterations,
    )
    return SignalTargetResult(target, tuple(dispositions), tuple(observations))
