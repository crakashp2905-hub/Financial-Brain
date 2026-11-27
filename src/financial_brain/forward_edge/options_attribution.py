"""Explain a single option position's realized premium P&L with an entry-Greek bridge.

The Greek terms are local first/second-order approximations from entry. The residual
contains higher-order/model effects; it is not described as pure market impact. Quote
execution against the two observed midpoints is reported separately, so every INR of
gross and net P&L reconciles without pretending the approximation is exact.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass


class OptionsAttributionError(ValueError):
    """Invalid option trade or non-reconciling premium attribution inputs."""


@dataclass(frozen=True)
class OptionTradeBridgeInput:
    position: str  # LONG or SHORT
    quantity: int
    contract_multiplier: float
    entry_underlying: float
    exit_underlying: float
    entry_delta: float
    entry_gamma: float
    entry_theta_per_day: float
    entry_vega_per_vol_point: float
    entry_iv_percent: float
    exit_iv_percent: float
    elapsed_days: float
    entry_bid: float
    entry_ask: float
    exit_bid: float
    exit_ask: float
    fees_inr: float


def decompose_option_trade_pnl(trade: OptionTradeBridgeInput) -> dict[str, object]:
    """Return a reconciled option P&L bridge in INR.

    Vega is supplied per one volatility point (for example, +1.0 means +1 vol point).
    Delta/gamma use the underlying price change; theta uses elapsed calendar days.
    Entry and exit executable prices are ask/bid for longs and bid/ask for shorts.
    """
    if trade.position not in {"LONG", "SHORT"}:
        raise OptionsAttributionError("position must be LONG or SHORT")
    if type(trade.quantity) is not int or trade.quantity <= 0:
        raise OptionsAttributionError("quantity must be a positive integer contract count")
    numeric = (trade.contract_multiplier, trade.entry_underlying, trade.exit_underlying,
               trade.entry_delta, trade.entry_gamma, trade.entry_theta_per_day,
               trade.entry_vega_per_vol_point, trade.entry_iv_percent,
               trade.exit_iv_percent, trade.elapsed_days, trade.entry_bid,
               trade.entry_ask, trade.exit_bid, trade.exit_ask, trade.fees_inr)
    if any(not math.isfinite(float(value)) for value in numeric):
        raise OptionsAttributionError("all option bridge inputs must be finite")
    if (trade.contract_multiplier <= 0 or trade.entry_underlying <= 0
            or trade.exit_underlying <= 0 or trade.elapsed_days < 0
            or trade.entry_iv_percent < 0 or trade.exit_iv_percent < 0
            or trade.fees_inr < 0
            or min(trade.entry_bid, trade.exit_bid) < 0
            or trade.entry_ask < trade.entry_bid or trade.exit_ask < trade.exit_bid):
        raise OptionsAttributionError("invalid multiplier, time, IV, fees, or crossed quote")

    sign = 1.0 if trade.position == "LONG" else -1.0
    units = trade.quantity * trade.contract_multiplier
    entry_mid = (trade.entry_bid + trade.entry_ask) / 2
    exit_mid = (trade.exit_bid + trade.exit_ask) / 2
    entry_exec = trade.entry_ask if sign > 0 else trade.entry_bid
    exit_exec = trade.exit_bid if sign > 0 else trade.exit_ask
    spot_change = trade.exit_underlying - trade.entry_underlying
    iv_change_points = trade.exit_iv_percent - trade.entry_iv_percent

    underlying = sign * trade.entry_delta * spot_change * units
    gamma = sign * 0.5 * trade.entry_gamma * spot_change**2 * units
    theta = sign * trade.entry_theta_per_day * trade.elapsed_days * units
    vega = sign * trade.entry_vega_per_vol_point * iv_change_points * units
    mid_to_mid = sign * (exit_mid - entry_mid) * units
    model_residual = mid_to_mid - underlying - gamma - theta - vega
    execution = sign * ((exit_exec - exit_mid) - (entry_exec - entry_mid)) * units
    gross = sign * (exit_exec - entry_exec) * units
    net = gross - trade.fees_inr
    explained = underlying + gamma + theta + vega + model_residual + execution - trade.fees_inr
    if not math.isclose(explained, net, rel_tol=0.0, abs_tol=1e-8):
        raise OptionsAttributionError("option P&L bridge failed reconciliation")

    fields: dict[str, object] = {
        "position": trade.position,
        "quantity_contracts": trade.quantity,
        "contract_multiplier": trade.contract_multiplier,
        "underlying_move_inr": underlying,
        "gamma_approximation_inr": gamma,
        "theta_decay_inr": theta,
        "implied_volatility_change_inr": vega,
        "model_residual_inr": model_residual,
        "bid_ask_execution_inr": execution,
        "gross_executable_pnl_inr": gross,
        "fees_inr": trade.fees_inr,
        "net_executable_pnl_inr": net,
        "reconciled_net_pnl_inr": explained,
        "reconciled": True,
        "approximation": "entry Greeks; second-order underlying term; no cross-gamma or higher-order terms",
        "model_residual_definition": "observed mid-to-mid premium change minus entry-Greek terms; includes higher-order and model effects",
        "vega_unit": "INR per volatility point; IV inputs are percent values",
    }
    fields["report_sha256"] = hashlib.sha256(json.dumps(
        fields, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()
    return fields
