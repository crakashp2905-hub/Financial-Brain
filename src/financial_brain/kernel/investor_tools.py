"""Investor and guardrail tools (FB-0352, FB-0360, FB-0361, FB-0377, FB-0378).

* ``factor_scores`` computes momentum (12-1 month return, skipping the latest month), low-risk (negative volatility)
  and reversal (negative 1-month return) from a price history. Insufficient history gives None, not a guess.
* ``buy_and_hold_with_dividends`` reinvests nothing: it returns cash dividends received and the final value after tax.
* ``dca_simulate`` invests a fixed amount each period at that period's price. Units accumulate; cash is never negative.
* ``ExposureGuard`` latches as soon as exposure exceeds its limit. Only ``reset_by_human`` clears it.
* ``flatten_all`` returns the orders that close every position, and cancels nothing it does not own.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from statistics import pstdev


def factor_scores(prices: Sequence[float]) -> dict[str, float | None]:
    n = len(prices)
    momentum = None
    if n >= 13:
        momentum = prices[-2] / prices[-13] - 1.0  # 12-1: skip the latest month
    reversal = None
    if n >= 2:
        reversal = -(prices[-1] / prices[-2] - 1.0)
    low_risk = None
    if n >= 21:
        rets = [prices[i] / prices[i - 1] - 1.0 for i in range(1, n)]
        low_risk = -pstdev(rets[-20:])
    return {"momentum": momentum, "reversal": reversal, "low_risk": low_risk}


def buy_and_hold_with_dividends(
    units: Decimal, final_price: Decimal, dividends_per_unit: Sequence[Decimal], tax_rate: Decimal
) -> dict[str, Decimal]:
    if tax_rate < 0 or tax_rate >= 1:
        raise ValueError("tax rate must be in [0, 1)")
    gross_div = units * sum(dividends_per_unit, Decimal(0))
    net_div = gross_div * (Decimal(1) - tax_rate)
    value = units * final_price
    return {"dividends_gross": gross_div, "dividends_net": net_div, "final_value": value, "total_net": value + net_div}


def dca_simulate(amount: Decimal, prices: Sequence[Decimal]) -> dict[str, Decimal]:
    if amount <= 0:
        raise ValueError("amount must be positive")
    units = Decimal(0)
    invested = Decimal(0)
    for price in prices:
        if price <= 0:
            raise ValueError("prices must be positive")
        units += amount / price
        invested += amount
    final_value = units * prices[-1] if prices else Decimal(0)
    return {"units": units, "invested": invested, "final_value": final_value}


@dataclass
class ExposureGuard:
    limit: Decimal
    latched: bool = False
    _breach_log: list[Decimal] = field(default_factory=list)

    def update(self, exposure: Decimal) -> bool:
        """Returns True while trading may continue. Once exposure exceeds the limit the guard latches."""
        if self.latched:
            return False
        if abs(exposure) > self.limit:
            self.latched = True
            self._breach_log.append(exposure)
            return False
        return True

    def reset_by_human(self, operator: str) -> None:
        if not operator:
            raise ValueError("a named human operator is required to reset")
        self.latched = False


def flatten_all(positions: dict[str, int], owned: set[str]) -> list[dict[str, object]]:
    orders: list[dict[str, object]] = []
    for symbol, qty in sorted(positions.items()):
        if symbol not in owned or qty == 0:
            continue
        orders.append({"symbol": symbol, "side": "SELL" if qty > 0 else "BUY", "quantity": abs(qty)})
    return orders
