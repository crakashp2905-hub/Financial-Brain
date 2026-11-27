"""A trade ticket for a human to place by hand. Nothing here is sent anywhere.

This system has no path to a broker, by design: Kite is wired for market data only, no order endpoint
exists in the package, and the decision ledger refuses to let an agent approve anything. So "trading
with a small amount of capital" is not something this system does. What it can do is the part before
the click: say, for an account of a given size, what a *paper* decision would look like as shares and
rupees, and print it as a ticket the account holder reads, judges and either places themselves or
ignores.

## Why a small account needs its own sizing

A 5% weight of a Rs 1 lakh account is Rs 5,000. That buys no Maruti (Rs 11,000 a share), 4 shares of
a Rs 1,200 stock, and the Rs 20 flat brokerage on an intraday leg is 0.4% of it. Weights that look fine
in a Rs 1 crore backtest round to nothing or to a different portfolio, and costs that were a rounding
error become the strategy. :func:`size_book` rounds *down* to whole shares, drops anything below a
minimum order, reports the cash it could not deploy, and prices each leg with the real cost schedule.
"""
from __future__ import annotations

from datetime import date
from math import floor, isfinite

from ..costs.india import CostModel, Segment, Side

MIN_ORDER_INR = 5_000.0
TICK = 0.05
BANNER = "NOT SENT ANYWHERE - for the account holder to place by hand, or not at all"


class TicketError(ValueError):
    pass


def _tick(x: float) -> float:
    return round(round(x / TICK) * TICK, 2)


def size_book(book: list[dict], *, capital_inr: float, prices: dict[str, float],
              min_order_inr: float = MIN_ORDER_INR, costs: CostModel | None = None) -> dict:
    """Allocate whole shares under cash plus entry-fee budget for a small account.

    Target weights remain targets, not promises: oversized aggregate targets are clipped
    by remaining cash. Expected exit-side costs are reported separately and are not
    double-counted against today's cash balance.
    """
    if not isfinite(capital_inr) or capital_inr <= 0:
        raise TicketError("capital must be finite and positive")
    if not isfinite(min_order_inr) or min_order_inr <= 0:
        raise TicketError("minimum order must be finite and positive")
    costs = costs or CostModel()
    lines, skipped = [], []
    spent, entry_fees = 0.0, 0.0
    for b in sorted(book, key=lambda x: -x["weight"]):
        ticker, weight = b.get("ticker"), b.get("weight")
        if not isinstance(ticker, str) or not ticker.strip():
            raise TicketError("every position requires a non-empty ticker")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) \
                or not isfinite(weight) or not 0 <= weight <= 1:
            raise TicketError(f"{ticker}: weight must be finite and within [0, 1]")
        px = prices.get(ticker)
        if px is None or isinstance(px, bool) or not isinstance(px, (int, float)) \
                or not isfinite(px) or px <= 0:
            skipped.append({"ticker": ticker, "why": "no finite positive price"})
            continue
        target = weight * capital_inr
        available = max(0.0, capital_inr - spent - entry_fees)
        shares = min(floor(target / px + 1e-12), floor(available / px + 1e-12))
        low, high = 0, shares
        while low < high:
            candidate_shares = (low + high + 1) // 2
            notional = candidate_shares * px
            entry = costs.one_way(turnover=notional, side=Side.BUY,
                segment=Segment.DELIVERY, bucket="large", include_impact=False)["total"]
            if notional + entry <= available + 1e-9:
                low = candidate_shares
            else:
                high = candidate_shares - 1
        shares = low
        notional = shares * px
        if shares < 1 or notional < min_order_inr:
            skipped.append({"ticker": b["ticker"], "why": f"Rs {target:,.0f} target buys "
                            f"{shares} share(s) = Rs {notional:,.0f}, below the Rs {min_order_inr:,.0f} minimum"})
            continue
        rt = costs.round_trip(turnover=notional, segment=Segment.DELIVERY, include_impact=False)
        entry = rt["buy"]["total"]
        lines.append({"ticker": b["ticker"], "shares": shares, "price": px, "notional": notional,
                      "weight": notional / capital_inr, "round_trip_fees": rt["total"],
                      "entry_fees": entry, "fees_pct": rt["total"] / notional})
        spent += notional
        entry_fees += entry
    fees_total = sum(l["round_trip_fees"] for l in lines)
    return {"capital": capital_inr, "positions": lines, "deployed": spent,
            "entry_fees_total": entry_fees, "cash_committed": spent + entry_fees,
            "cash": max(0.0, capital_inr - spent - entry_fees), "skipped": skipped,
            "fees_total": fees_total}


def ticket(*, symbol: str, side: str, quantity: int, reference_price: float, limit_buffer: float = 0.002,
           stop_price: float | None = None, rationale: str = "", decision_id: str | None = None,
           as_of: date | None = None) -> dict:
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise TicketError(f"side must be BUY or SELL, got {side!r}")
    if quantity < 1 or reference_price <= 0:
        raise TicketError("quantity and price must be positive")
    limit = _tick(reference_price * (1 + limit_buffer if side == "BUY" else 1 - limit_buffer))
    if stop_price is not None and ((side == "BUY" and stop_price >= limit)
                                   or (side == "SELL" and stop_price <= limit)):
        raise TicketError("the stop must be on the losing side of the limit")
    notional = quantity * limit
    fees = CostModel().round_trip(turnover=notional, segment=Segment.DELIVERY, include_impact=False)
    t = {"banner": BANNER, "symbol": symbol, "side": side, "quantity": quantity,
         "type": "LIMIT (delivery)", "limit_price": limit, "reference_price": reference_price,
         "stop_price": _tick(stop_price) if stop_price else None, "notional": notional,
         "round_trip_fees": fees["total"], "decision_id": decision_id, "as_of": as_of,
         "rationale": rationale}
    t["text"] = "\n".join([
        f"*** {BANNER} ***",
        f"{side} {quantity} x {symbol}  LIMIT {limit:.2f}  (reference {reference_price:.2f}, delivery)",
        f"notional Rs {notional:,.0f}   round-trip statutory + broker fees ~Rs {fees['total']:,.0f}"
        f" ({fees['total'] / notional:.2%})",
        f"stop (invalidation) {t['stop_price']:.2f}" if t["stop_price"] else "no stop set",
        f"why: {rationale or 'n/a'}", f"decision: {decision_id or 'n/a'}   as of: {as_of or 'n/a'}",
        "This is information, not advice. A paper decision has no track record yet."])
    return t
