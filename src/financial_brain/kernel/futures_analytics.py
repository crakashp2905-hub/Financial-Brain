"""Futures analytics (FB-0319, FB-0320, FB-0321, FB-0323, FB-0324).

* ``calendar_spread`` is back minus front. A positive spread is contango, negative is backwardation.
* ``fair_futures`` is a simple cost-of-carry price; ``basis_vs_fair`` is the market futures price minus that fair value.
* ``hedge_contracts`` sizes futures to offset a position value, rounding toward zero so the hedge never over-hedges.
* ``expiry_settlement`` pays cash for cash-settled contracts. Physically delivered contracts are refused unless the
  holder has the delivery quantity, and the result says so rather than pretending to deliver.
* ``within_daily_limit`` checks a price against the prior settlement plus or minus the exchange limit.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN


def calendar_spread(front: Decimal, back: Decimal) -> Decimal:
    return back - front


def fair_futures(spot: Decimal, rate: Decimal, dividend_yield: Decimal, days: int) -> Decimal:
    if days < 0:
        raise ValueError("days must be non-negative")
    return spot * (Decimal(1) + (rate - dividend_yield) * Decimal(days) / Decimal(365))


def basis_vs_fair(futures: Decimal, spot: Decimal, rate: Decimal, dividend_yield: Decimal, days: int) -> Decimal:
    return futures - fair_futures(spot, rate, dividend_yield, days)


def hedge_contracts(position_value: Decimal, contract_notional: Decimal) -> int:
    if contract_notional <= 0:
        raise ValueError("contract notional must be positive")
    raw = (position_value / contract_notional).to_integral_value(rounding=ROUND_DOWN)
    return int(raw)


def expiry_settlement(method: str, quantity: int, settle_price: Decimal, entry_price: Decimal, held_delivery_qty: int) -> dict[str, object]:
    if method == "CASH":
        return {"cash": (settle_price - entry_price) * Decimal(quantity), "delivered": 0, "status": "CASH_SETTLED"}
    if method == "PHYSICAL":
        if held_delivery_qty < quantity:
            return {"cash": Decimal(0), "delivered": 0, "status": "DELIVERY_SHORTFALL"}
        return {"cash": Decimal(0), "delivered": quantity, "status": "DELIVERED"}
    raise ValueError("method must be CASH or PHYSICAL")


def within_daily_limit(price: Decimal, prior_settle: Decimal, limit_pct: Decimal) -> bool:
    if prior_settle <= 0 or limit_pct <= 0:
        raise ValueError("prior settlement and limit must be positive")
    return abs(price - prior_settle) / prior_settle <= limit_pct
