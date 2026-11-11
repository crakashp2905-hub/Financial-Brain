"""Derivatives extras (FB-0300, FB-0302, FB-0306, FB-0315, FB-0325).

* ``strategy_payoff`` sums expiry payoffs of option legs. Verticals, straddles and strangles are just leg sets.
* ``delta_hedge`` sizes an underlying hedge to neutralise delta and charges half-spread slippage on the hedge.
* ``executable_net`` prices a multi-leg order from executable prices: buys pay the ask, sells receive the bid. It also
  reports the legging spread, the cost of crossing every leg, not just the midprice.
* ``contract_notional`` and ``lot_ok`` check per-contract size and lot rules before a ticket is built.
* ``futures_tsmom`` is the time-series momentum futures baseline, applied from the next bar.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class OptionLeg:
    kind: str  # CALL | PUT
    strike: Decimal
    quantity: int  # signed: positive long, negative short
    bid: Decimal
    ask: Decimal

    def __post_init__(self) -> None:
        if self.kind not in ("CALL", "PUT"):
            raise ValueError("kind must be CALL or PUT")
        if self.bid > self.ask or self.bid < 0:
            raise ValueError("invalid bid and ask")


def strategy_payoff(legs: Sequence[OptionLeg], spot: Decimal) -> Decimal:
    total = Decimal(0)
    for leg in legs:
        intrinsic = max(spot - leg.strike, Decimal(0)) if leg.kind == "CALL" else max(leg.strike - spot, Decimal(0))
        total += intrinsic * Decimal(leg.quantity)
    return total


def delta_hedge(portfolio_delta: Decimal, underlying_bid: Decimal, underlying_ask: Decimal) -> dict[str, Decimal]:
    """Shares to trade to neutralise delta (rounded toward zero), plus half-spread cost per share traded."""
    if underlying_bid <= 0 or underlying_ask < underlying_bid:
        raise ValueError("invalid underlying quote")
    shares = -portfolio_delta.to_integral_value(rounding="ROUND_DOWN")
    mid = (underlying_bid + underlying_ask) / 2
    per_share_cost = (underlying_ask - underlying_bid) / 2
    residual = portfolio_delta + Decimal(shares)
    return {"shares": shares, "residual_delta": residual, "cost": abs(shares) * per_share_cost, "mid": mid}


def executable_net(legs: Sequence[OptionLeg]) -> dict[str, Decimal]:
    """Net debit (positive) or credit (negative) at executable prices, the midprice, and the legging spread."""
    executable = Decimal(0)
    mid = Decimal(0)
    spread_cost = Decimal(0)
    for leg in legs:
        q = Decimal(leg.quantity)
        leg_mid = (leg.bid + leg.ask) / 2
        if leg.quantity > 0:
            executable += q * leg.ask
        else:
            executable += q * leg.bid
        mid += q * leg_mid
        spread_cost += abs(q) * (leg.ask - leg.bid)
    return {"executable": executable, "mid": mid, "legging_spread": spread_cost}


def contract_notional(price: Decimal, multiplier: Decimal, quantity: int) -> Decimal:
    if multiplier <= 0 or price <= 0:
        raise ValueError("price and multiplier must be positive")
    return price * multiplier * Decimal(quantity)


def lot_ok(quantity: int, lot: int) -> bool:
    if lot <= 0:
        raise ValueError("lot must be positive")
    return quantity != 0 and quantity % lot == 0


def futures_tsmom(closes: Sequence[float], lookback: int) -> list[int]:
    if lookback < 1:
        raise ValueError("lookback must be positive")
    raw = [0] * len(closes)
    for i in range(lookback, len(closes)):
        raw[i] = 1 if closes[i] > closes[i - lookback] else 0
    return [0] + raw[:-1]
