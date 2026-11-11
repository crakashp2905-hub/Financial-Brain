"""Futures contract selection and rolls, and FX cross and exposure checks (FB-0093, FB-0316..0318, FB-0328, FB-0341, FB-0344, FB-0348, FB-0349).

Futures
* ``front_contract`` returns the nearest unexpired contract on a date. Expired contracts are never returned.
* ``roll_date``: the roll happens on or before the last trading day of the front contract, never after it.
* Back-adjusted continuous prices are analytic only; ``fill_price_source`` refuses them for execution.

FX
* ``triangular_gap``: EURUSD x USDJPY versus EURJPY. A gap is a consistency alert, never an executable arbitrage.
* ``quote_is_fresh``: stale quotes block orders.
* ``exposure_matrix``: net currency exposure aggregated across pairs (base long plus quote short).
* ``leverage_ok``: a leverage cap per venue policy.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class Contract:
    symbol: str
    last_trading_day: date


def front_contract(contracts: list[Contract], on: date) -> Contract:
    live = sorted((c for c in contracts if c.last_trading_day >= on), key=lambda c: c.last_trading_day)
    if not live:
        raise ContractError(f"no unexpired contract on {on.isoformat()}; refusing to trade")
    return live[0]


def roll_date(front: Contract, roll_days_before_expiry: int = 2) -> date:
    if roll_days_before_expiry < 0:
        raise ContractError("roll offset must be non-negative")
    return front.last_trading_day - timedelta(days=roll_days_before_expiry)


def fill_price_source(kind: str) -> str:
    """Only raw traded prices of the specific contract may be used for fills."""
    if kind == "BACK_ADJUSTED":
        raise ContractError("back-adjusted continuous prices are analytic only and cannot fill orders")
    if kind != "RAW_CONTRACT":
        raise ContractError(f"unknown price source {kind}")
    return kind


def triangular_gap(eurusd: float, usdjpy: float, eurjpy: float) -> float:
    """Relative gap between EURJPY and the synthetic EURUSD x USDJPY. Used for alerts only."""
    if min(eurusd, usdjpy, eurjpy) <= 0:
        raise ContractError("quotes must be positive")
    synthetic = eurusd * usdjpy
    return (eurjpy - synthetic) / synthetic


def quote_is_fresh(age_seconds: float, max_age_seconds: float) -> bool:
    return age_seconds is not None and 0 <= age_seconds <= max_age_seconds


def exposure_matrix(positions: list[tuple[str, Decimal, int]]) -> dict[str, Decimal]:
    """Net exposure per currency. Each position is (pair 'BASEQUOTE', price, units); base long, quote short."""
    net: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for pair, price, units in positions:
        if len(pair) != 6:
            raise ContractError(f"bad pair {pair}")
        base, quote = pair[:3], pair[3:]
        net[base] += Decimal(units)
        net[quote] -= Decimal(units) * price
    return dict(net)


def leverage_ok(gross_notional: Decimal, equity: Decimal, max_leverage: Decimal) -> bool:
    if equity <= 0:
        return False
    return gross_notional / equity <= max_leverage
