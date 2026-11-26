"""Fixed-book account-size economics sweep; never optimizes strategy parameters."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from financial_brain.costs.india import CostModel, Segment
from financial_brain.execution.ticket import size_book


class AccountSizeSweepError(ValueError):
    """Invalid size-sweep assumptions or gross P&L inputs."""


@dataclass(frozen=True)
class AccountSizePoint:
    account_size_inr: float
    deployed_inr: float
    cash_inr: float
    shares_by_symbol: tuple[tuple[str, int], ...]
    gross_pnl_inr: float
    brokerage_inr: float
    statutory_taxes_inr: float
    other_charges_inr: float
    impact_estimate_inr: float
    net_pnl_inr: float
    return_on_total_capital: float
    return_on_deployed_capital: float | None


@dataclass(frozen=True)
class AccountSizeSweep:
    points: tuple[AccountSizePoint, ...]
    book_sha256: str
    cost_schedule_sha256: str
    report_sha256: str
    strategy_weights_optimized: bool = False


def sweep_account_sizes(*, book: list[dict], prices: dict[str, float],
                        gross_return_by_symbol: dict[str, float],
                        account_sizes_inr: tuple[float, ...],
                        costs: CostModel | None = None,
                        include_impact: bool = True) -> AccountSizeSweep:
    """Apply one frozen weight book to multiple account balances and itemize cost classes.

    Gross returns are fixed caller inputs; the function never tunes strategy weights or
    chooses the best account size. Fixed order charges flow through the cost schedule,
    while taxes, brokerage, other statutory charges and impact are shown separately.
    """
    if not book or not account_sizes_inr or len(set(account_sizes_inr)) != len(account_sizes_inr):
        raise AccountSizeSweepError("fixed book and unique account sizes are required")
    if any(not math.isfinite(value) or value <= 0 for value in account_sizes_inr):
        raise AccountSizeSweepError("account sizes must be finite and positive")
    if any(symbol not in gross_return_by_symbol or not math.isfinite(gross_return_by_symbol[symbol])
           for symbol in (row.get("ticker") for row in book)):
        raise AccountSizeSweepError("finite gross return is required for every frozen book symbol")
    model = costs or CostModel()
    cost_schedule = model.as_dict()
    cost_hash = hashlib.sha256(json.dumps(cost_schedule, sort_keys=True, separators=(",", ":"),
                                          allow_nan=False).encode()).hexdigest()
    book_hash = hashlib.sha256(json.dumps(book, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()
    points = []
    for capital in sorted(account_sizes_inr):
        ticket = size_book(book, capital_inr=capital, prices=prices, costs=model)
        gross = 0.0
        brokerage = statutory = other = impact = 0.0
        shares = []
        for position in ticket["positions"]:
            symbol, notional = position["ticker"], position["notional"]
            shares.append((symbol, position["shares"]))
            gross += notional * gross_return_by_symbol[symbol]
            charge = model.round_trip(turnover=notional, segment=Segment.DELIVERY, include_impact=False)
            brokerage += charge["buy"]["brokerage"] + charge["sell"]["brokerage"]
            statutory += sum(charge["buy"][name] + charge["sell"][name]
                             for name in ("stt", "stamp", "exchange", "sebi", "gst"))
            other += charge["buy"].get("dp", 0.0) + charge["sell"].get("dp", 0.0)
            if include_impact:
                impact += model.round_trip(turnover=notional, segment=Segment.DELIVERY,
                                           include_impact=True)["total"] - charge["total"]
        cost_total = brokerage + statutory + other + impact
        net = gross - cost_total
        deployed = ticket["deployed"]
        points.append(AccountSizePoint(capital, deployed, ticket["cash"], tuple(shares), gross,
                                       brokerage, statutory, other, impact, net, net / capital,
                                       net / deployed if deployed > 0 else None))
    body = {"points": [point.__dict__ for point in points], "book_sha256": book_hash,
            "cost_schedule_sha256": cost_hash, "strategy_weights_optimized": False}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return AccountSizeSweep(tuple(points), book_hash, cost_hash, digest)
