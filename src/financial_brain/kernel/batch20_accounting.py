"""Accounting and tax postings (FB-0235, FB-0237, FB-0241, FB-0243, FB-0247, FB-0262, FB-0267).

* ``dividend_postings`` split gross dividend into net cash and withholding. The two sides always sum to gross.
* ``option_exercise_flows`` give cash and share movement on exercise or assignment. Long exercise pays strike; short
  assignment receives it.
* ``consolidated_total`` sums balances across accounts in several currencies at given rates. A missing rate is refused.
* ``excess_pnl`` is portfolio return minus benchmark return.
* ``tax_estimate`` applies a country rate plugin and is always labelled ESTIMATE. Unknown countries are refused.
* ``fifo_realised`` consumes tax lots first-in first-out and returns the realised gain and the remaining lots.
* ``contribution_path`` simulates recurring contributions and withdrawals; the balance may never go negative.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal


def dividend_postings(units: Decimal, dividend_per_unit: Decimal, withholding_rate: Decimal) -> dict[str, Decimal]:
    if not (Decimal(0) <= withholding_rate < Decimal(1)):
        raise ValueError("withholding rate must be in [0, 1)")
    gross = units * dividend_per_unit
    tax = gross * withholding_rate
    net = gross - tax
    assert net + tax == gross
    return {"dividend_income": gross, "cash": net, "withholding": tax}


def option_exercise_flows(
    side: str, quantity: int, strike: Decimal, multiplier: Decimal
) -> dict[str, Decimal]:
    shares = Decimal(quantity) * multiplier
    cash = strike * shares
    if side == "LONG_EXERCISE":
        return {"cash": -cash, "shares": shares}
    if side == "SHORT_ASSIGNED":
        return {"cash": cash, "shares": -shares}
    raise ValueError("side must be LONG_EXERCISE or SHORT_ASSIGNED")


def consolidated_total(balances: Mapping[str, Mapping[str, Decimal]], rates: Mapping[str, Decimal]) -> Decimal:
    total = Decimal(0)
    for account in balances.values():
        for currency, amount in account.items():
            if currency not in rates:
                raise KeyError(f"no rate for {currency}")
            total += amount * rates[currency]
    return total


def excess_pnl(portfolio_return: float, benchmark_return: float) -> float:
    return portfolio_return - benchmark_return


TAX_RATES: dict[str, Decimal] = {"IN": Decimal("0.15"), "US": Decimal("0.20")}


def tax_estimate(gain: Decimal, country: str) -> dict[str, object]:
    if country not in TAX_RATES:
        raise KeyError(f"no tax plugin for {country}")
    return {"tax": max(gain, Decimal(0)) * TAX_RATES[country], "label": "ESTIMATE"}


def fifo_realised(lots: Sequence[tuple[Decimal, Decimal]], sell_qty: Decimal, sell_price: Decimal) -> tuple[Decimal, list[tuple[Decimal, Decimal]]]:
    remaining = list(lots)
    to_sell = sell_qty
    gain = Decimal(0)
    if sum((q for q, _ in lots), Decimal(0)) < sell_qty:
        raise ValueError("cannot sell more than held")
    while to_sell > 0:
        qty, cost = remaining[0]
        take = min(qty, to_sell)
        gain += take * (sell_price - cost)
        to_sell -= take
        if take == qty:
            remaining.pop(0)
        else:
            remaining[0] = (qty - take, cost)
    return gain, remaining


def contribution_path(
    start: Decimal, monthly_contribution: Decimal, monthly_withdrawal: Decimal, monthly_growth: Decimal, months: int
) -> Decimal:
    balance = start
    for _ in range(months):
        balance = balance * (Decimal(1) + monthly_growth) + monthly_contribution - monthly_withdrawal
        if balance < 0:
            raise ValueError("withdrawals exceed balance")
    return balance
