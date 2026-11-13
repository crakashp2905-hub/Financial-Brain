"""Convert common signal/net-alpha observations to one research-only target portfolio (PE-0060)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Mapping

import numpy as np

from financial_brain.decisions.net_alpha import NetAlpha
from .target_portfolio import TargetPortfolio, plan_target_portfolio
from .uncertainty_sizing import (UncertaintySizingPolicy, UncertaintySizingError,
                                 uncertainty_size_multiplier)

SIGNAL_ACTIONS = frozenset({"BUY", "HOLD", "SELL", "NO_SIGNAL"})


class SignalAllocatorError(ValueError):
    pass


@dataclass(frozen=True)
class SignalTargetResult:
    target: TargetPortfolio
    dispositions: tuple[tuple[str, str, float], ...]
    research_only: bool = True
    uncertainty_multipliers: tuple[tuple[str, float], ...] = ()


def allocate_signals(*, as_of: date, scheduled_rebalances: frozenset[date],
                     signals: Mapping[str, tuple[str, NetAlpha]], covariance: np.ndarray,
                     covariance_names: tuple[str, ...], previous_weights: Mapping[str, float],
                     min_forecast_net_alpha: float, max_name_weight: float = 0.10,
                     cash_floor: float = 0.0, max_one_way_turnover: float = 0.20,
                     turnover_cost_per_unit: float = 0.0, no_trade_band: float = 0.005,
                     max_positions: int = 20, sectors: Mapping[str, str] | None = None,
                     max_sector_weight: float = 0.30,
                     uncertainty_standard_errors: Mapping[str, float] | None = None,
                     uncertainty_policy: UncertaintySizingPolicy | None = None,
                     risk_aversion: float = 1.0,
                     iterations: int = 500) -> SignalTargetResult:
    """Route every named signal through one allocator; input NetAlpha already itemizes trade costs.

    The threshold is a per-horizon expected-return hurdle, not risk-adjusted utility. SELL signals
    receive a negative target score; HOLD/NO_SIGNAL carry no new-entry score. Every held name must
    be represented so the allocator cannot silently orphan an existing position.
    """
    names = tuple(sorted(signals))
    if not names or set(names) != set(covariance_names) or len(set(covariance_names)) != len(covariance_names):
        raise SignalAllocatorError("signals and covariance universe must contain the same unique names")
    if not isfinite(min_forecast_net_alpha) or min_forecast_net_alpha < 0:
        raise SignalAllocatorError("minimum forecast net-alpha hurdle must be finite and non-negative")
    held = set(previous_weights) - {"CASH"}
    if not held.issubset(signals):
        raise SignalAllocatorError("every held instrument needs an explicit signal observation")
    horizons = set()
    alpha: dict[str, float] = {}
    dispositions = []
    for name in names:
        action, forecast = signals[name]
        if action not in SIGNAL_ACTIONS or not isinstance(forecast, NetAlpha):
            raise SignalAllocatorError(f"{name}: expected a supported action and NetAlpha forecast")
        horizons.add(forecast.horizon)
        if action == "SELL":
            score, reason = -max(abs(forecast.net), min_forecast_net_alpha, 1e-12), "SELL_SIGNAL"
        elif action in {"HOLD", "NO_SIGNAL"}:
            score, reason = 0.0, action
        elif forecast.net <= min_forecast_net_alpha:
            score, reason = 0.0, "NO_TRADE_NET_ALPHA_HURDLE"
        else:
            score, reason = forecast.net, "NET_ALPHA_ABOVE_HURDLE"
        alpha[name] = score
        dispositions.append((name, reason, forecast.net))
    if len(horizons) != 1:
        raise SignalAllocatorError("all forecasts in one target book must share the same holding horizon")
    if (uncertainty_policy is None) != (uncertainty_standard_errors is None):
        raise SignalAllocatorError("uncertainty standard errors and a frozen sizing policy must be supplied together")
    if uncertainty_standard_errors is not None and set(uncertainty_standard_errors) != set(names):
        raise SignalAllocatorError("uncertainty standard errors must cover the frozen signal universe")
    multipliers: dict[str, float] = {}
    if uncertainty_policy is not None and uncertainty_standard_errors is not None:
        try:
            multipliers = {
                name: uncertainty_size_multiplier(
                    expected_net_alpha=signals[name][1].net,
                    uncertainty_standard_error=float(uncertainty_standard_errors[name]),
                    policy=uncertainty_policy,
                )
                for name in names
            }
        except (TypeError, ValueError, UncertaintySizingError) as exc:
            raise SignalAllocatorError(str(exc)) from exc
    target = plan_target_portfolio(
        as_of=as_of, scheduled_rebalances=scheduled_rebalances,
        expected_net_alpha={name: alpha[name] * multipliers.get(name, 1.0) for name in names},
        covariance=covariance, covariance_names=tuple(sorted(covariance_names)),
        previous_weights=previous_weights, max_name_weight=max_name_weight,
        cash_floor=cash_floor, max_one_way_turnover=max_one_way_turnover,
        turnover_cost_per_unit=turnover_cost_per_unit, no_trade_band=no_trade_band,
        max_positions=max_positions, sectors=sectors, max_sector_weight=max_sector_weight,
        risk_aversion=risk_aversion,
        iterations=iterations,
    )
    return SignalTargetResult(target, tuple(dispositions), uncertainty_multipliers=tuple(sorted(multipliers.items())))
