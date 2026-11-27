"""Portfolio-level option Greek and correlated gap/settlement stress estimates."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite


class OptionsPortfolioStressError(ValueError):
    """Incomplete, stale or invalid option portfolio stress input."""


@dataclass(frozen=True, slots=True)
class OptionRiskPosition:
    position_id: str
    strategy_id: str
    underlying: str
    settlement_at: datetime
    option_type: str
    signed_contracts: int
    lot_size: int
    strike: float
    spot: float
    mark_per_unit: float
    delta: float
    gamma: float
    vega_per_vol_point: float
    theta_per_day: float
    iv: float
    fees_per_contract_inr: float
    quote_at: datetime
    quote_source_sha256: str


@dataclass(frozen=True, slots=True)
class PortfolioShock:
    shock_id: str
    horizon_at: datetime
    underlying_return: tuple[tuple[str, float], ...]
    iv_change: tuple[tuple[str, float], ...]


def stress_option_portfolio(
    positions: tuple[OptionRiskPosition, ...],
    scenarios: tuple[PortfolioShock, ...],
    *,
    as_of: datetime,
    max_quote_age: timedelta = timedelta(seconds=30),
) -> dict:
    """Return whole-book Greeks, concentration and worst correlated scenario net loss.

    Greek marks use delta/gamma/vega/theta for non-expiry horizons. Positions settling
    by the horizon use intrinsic payoff. Each scenario provides a common shock per
    underlying, so every leg/strategy on that underlying shares the same gap/settlement
    path. No broker margin or real-market calibration is implied.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None or max_quote_age < timedelta(0):
        raise OptionsPortfolioStressError("as_of must be aware and quote-age bound nonnegative")
    if not positions or not scenarios:
        raise OptionsPortfolioStressError("positions and stress scenarios are required")
    ids = [position.position_id for position in positions]
    if len(ids) != len(set(ids)):
        raise OptionsPortfolioStressError("portfolio position ids must be unique")
    underlyings = {position.underlying for position in positions}
    for position in positions:
        if (not position.position_id or not position.strategy_id or not position.underlying
                or position.option_type not in {"CE", "PE"} or position.signed_contracts == 0
                or position.lot_size < 1 or position.settlement_at.tzinfo is None
                or position.settlement_at.utcoffset() is None):
            raise OptionsPortfolioStressError("option position identity or contract is invalid")
        if (position.quote_at.tzinfo is None or position.quote_at > as_of
                or as_of - position.quote_at > max_quote_age):
            raise OptionsPortfolioStressError("option quote is missing, future or stale")
        numeric = (position.strike, position.spot, position.mark_per_unit,
                   position.delta, position.gamma, position.vega_per_vol_point,
                   position.theta_per_day, position.iv, position.fees_per_contract_inr)
        if not all(isfinite(value) for value in numeric) or min(
                position.strike, position.spot, position.mark_per_unit) <= 0:
            raise OptionsPortfolioStressError("option marks, Greeks and costs must be finite")
        if not -1 <= position.delta <= 1 or position.gamma < 0 or position.iv <= 0:
            raise OptionsPortfolioStressError("option delta, gamma or IV is outside valid bounds")
        if position.vega_per_vol_point < 0:
            raise OptionsPortfolioStressError("long-option vega convention is invalid")
        if position.fees_per_contract_inr < 0:
            raise OptionsPortfolioStressError("option fees cannot be negative")
        if not re.fullmatch(r"[0-9a-f]{64}", position.quote_source_sha256):
            raise OptionsPortfolioStressError("option quote source hash is required")

    net_delta: dict[str, float] = {}
    gross_delta: dict[str, float] = {}
    net_gamma: dict[str, float] = {}
    net_vega: dict[str, float] = {}
    gross_notional: dict[str, float] = {}
    by_expiry: dict[str, float] = {}
    for position in positions:
        scale = position.signed_contracts * position.lot_size
        delta = scale * position.spot * position.delta
        net_delta[position.underlying] = net_delta.get(position.underlying, 0.0) + delta
        gross_delta[position.underlying] = gross_delta.get(position.underlying, 0.0) + abs(delta)
        net_gamma[position.underlying] = net_gamma.get(position.underlying, 0.0) + scale * position.gamma
        net_vega[position.underlying] = net_vega.get(position.underlying, 0.0) + scale * position.vega_per_vol_point
        gross_notional[position.underlying] = gross_notional.get(position.underlying, 0.0) + abs(
            scale * position.spot * position.delta)
        settlement_key = position.settlement_at.isoformat()
        by_expiry[settlement_key] = by_expiry.get(settlement_key, 0.0) + abs(
            scale * position.spot * position.delta)

    scenario_results = []
    seen_scenarios: set[str] = set()
    for scenario in scenarios:
        if (not scenario.shock_id or scenario.shock_id in seen_scenarios
                or scenario.horizon_at.tzinfo is None or scenario.horizon_at < as_of):
            raise OptionsPortfolioStressError("scenario identity or horizon is invalid")
        seen_scenarios.add(scenario.shock_id)
        if (len(dict(scenario.underlying_return)) != len(scenario.underlying_return)
                or len(dict(scenario.iv_change)) != len(scenario.iv_change)):
            raise OptionsPortfolioStressError("duplicate underlying shocks are invalid")
        returns = dict(scenario.underlying_return)
        iv_changes = dict(scenario.iv_change)
        if set(returns) != underlyings or set(iv_changes) != underlyings:
            raise OptionsPortfolioStressError("each stress scenario must price every underlying and IV shock")
        if any(not isfinite(value) or value <= -1 for value in returns.values()):
            raise OptionsPortfolioStressError("underlying stress returns must be finite and exceed -100%")
        if any(not isfinite(value) for value in iv_changes.values()):
            raise OptionsPortfolioStressError("IV shocks must be finite")
        total_pnl = 0.0
        scenario_costs = 0.0
        for position in positions:
            spot_move = position.spot * returns[position.underlying]
            stressed_spot = position.spot + spot_move
            days = (scenario.horizon_at - as_of).total_seconds() / 86400
            if scenario.horizon_at >= position.settlement_at:
                future_mark = (max(0.0, stressed_spot - position.strike)
                               if position.option_type == "CE"
                               else max(0.0, position.strike - stressed_spot))
            else:
                if position.iv + iv_changes[position.underlying] < 0:
                    raise OptionsPortfolioStressError("stress would imply negative implied volatility")
                future_mark = max(0.0, position.mark_per_unit
                    + position.delta * spot_move + 0.5 * position.gamma * spot_move ** 2
                    + position.vega_per_vol_point * iv_changes[position.underlying]
                    + position.theta_per_day * days)
            scale = position.signed_contracts * position.lot_size
            total_pnl += scale * (future_mark - position.mark_per_unit)
            scenario_costs += abs(position.signed_contracts) * position.fees_per_contract_inr
        scenario_results.append({"scenario_id": scenario.shock_id,
                                 "gross_pnl_inr": total_pnl,
                                 "fees_inr": scenario_costs,
                                 "net_pnl_inr": total_pnl - scenario_costs,
                                 "net_loss_inr": max(0.0, scenario_costs - total_pnl)})
    worst = min(scenario_results, key=lambda row: (row["net_pnl_inr"], row["scenario_id"]))
    return {"as_of": as_of, "position_count": len(positions), "underlyings": sorted(underlyings),
            "net_delta_inr_by_underlying": net_delta,
            "gross_delta_inr_by_underlying": gross_delta,
            "net_gamma_by_underlying": net_gamma,
            "net_vega_per_vol_point_by_underlying": net_vega,
            "gross_delta_notional_by_underlying": gross_notional,
            "gross_delta_notional_by_settlement_at": by_expiry,
            "scenario_results": scenario_results,
            "quote_source_sha256_by_position": {
                position.position_id: position.quote_source_sha256 for position in positions
            },
            "worst_scenario_id": worst["scenario_id"],
            "worst_net_loss_inr": worst["net_loss_inr"],
            "settlement_scenarios_included": True}
