"""A trade plan: how much, for how long, and where to get out - and whether there is any edge to plan around.

For one name and one trading *style* this answers the three questions a trader has to answer before
anything else, from rules and numbers that are written down rather than felt:

    position size   risk-based: the rupee loss you accept if the stop is hit, divided by the distance to the
                    stop, then capped by a maximum weight and by what the name's turnover can absorb
    time horizon    from the style's measured information horizon, not from the calendar
    exits           a stop (volatility-scaled), a time exit, and a signal exit; no profit target, because
                    the exit study found targets and stops add risk control, not return

and it attaches the **evidence for the style**, because a plan for a style with no demonstrated edge is a plan
for paying costs. The status comes from :data:`EDGE`, which is updated from the result notes; a style whose
status is ``NONE_DEMONSTRATED`` still gets a plan (the arithmetic is the same) but the plan says so first.

This is a template computed from public statistics. It does not know the account holder's income, needs,
tax position or other holdings, so it is information, not advice, and nothing in it is sent to a broker.
"""
from __future__ import annotations

from datetime import date

import numpy as np

from ..costs.india import CostModel, Segment

NONE_DEMONSTRATED = "NONE_DEMONSTRATED"
WEAK_IN_SAMPLE = "WEAK_IN_SAMPLE"

#: What each style's evidence says, and where. Updated from the result notes in brain/07-Research.
EDGE = {
    "position": {
        "status": WEAK_IN_SAMPLE,
        "evidence": "Momentum sleeve +0.47%/month net of sized costs (t 2.5) and a composite whose IC keeps "
                    "rising to 120 sessions; found in-sample, not yet confirmed by live paper decisions.",
        "source": "Trader layers built, and what they showed; Result of half-life and second-order spillover"},
    "swing": {
        "status": NONE_DEMONSTRATED,
        "evidence": "Pullback, oversold, breakout and strength setups earn +0.2% to -0.15% gross over a random "
                    "liquid name over 5-10 sessions (none significant) and lose 0.2-0.6% a trade net of a "
                    "~0.4% round trip.",
        "source": "Result of swing setups"},
    "intraday": {
        "status": NONE_DEMONSTRATED,
        "evidence": "Opening-range breakout with a stop and 2R target: gross -0.2 to +6 bp a trade, net -15 to "
                    "-21 bp (t -8 to -17) over 251,000 simulated trades; first-hour features earned 0.5 bp "
                    "against 14-54 bp of cost.",
        "source": "Result of the opening-range breakout with stops"},
}

STYLES = {
    # horizon in sessions; stop_k = stop distance in volatility units over the stop window; stop_window sessions
    "position": {"horizon": 90, "review": 20, "stop_k": 2.5, "stop_window": 20, "segment": Segment.DELIVERY,
                 "signal_exit": "exit at a monthly review if the composite has fallen into the bottom 20%"},
    "swing": {"horizon": 7, "review": 1, "stop_k": 2.0, "stop_window": 7, "segment": Segment.DELIVERY,
              "signal_exit": "exit if the setup that triggered the entry no longer holds at the close"},
    "intraday": {"horizon": 0, "review": 0, "stop_k": 1.0, "stop_window": 1, "segment": Segment.INTRADAY,
                 "signal_exit": "flat by 15:15 IST, no overnight position"},
}
MAX_WEIGHT = 0.10
PARTICIPATION = 0.10
MIN_ORDER_INR = 5_000.0
#: Fraction of the account lost if the stop is hit, by style: a position held for months is sized to a wider
#: stop and a larger budget; an intraday trade that repeats daily gets a small one.
DEFAULT_RISK = {"position": 0.01, "swing": 0.005, "intraday": 0.0025}
MAX_STOP = 0.25


class PlanError(ValueError):
    pass


def _add_sessions(d: date, n: int) -> date:
    return date.fromordinal(int(np.busday_offset(d.isoformat(), n, roll="forward").astype("datetime64[D]").astype(int)) + 719163)


def plan(*, ticker: str, price: float, vol_annual: float, adv_inr: float, capital_inr: float,
         style: str = "position", risk_per_trade: float | None = None, atr_pct: float | None = None,
         max_weight: float = MAX_WEIGHT, as_of: date | None = None, side: str = "long",
         bucket: str = "large") -> dict:
    if style not in STYLES:
        raise PlanError(f"style must be one of {sorted(STYLES)}")
    if min(price, vol_annual, adv_inr, capital_inr) <= 0:
        raise PlanError("price, volatility, turnover and capital must be positive")
    if risk_per_trade is None:
        risk_per_trade = DEFAULT_RISK[style]
    if not 0 < risk_per_trade <= 0.02:
        raise PlanError("risk per trade must be between 0 and 2% of the account")
    if side not in ("long", "short"):
        raise PlanError("side must be long or short")
    if side == "short" and style != "intraday":
        raise PlanError("short positions are intraday only in the cash market")
    s = STYLES[style]
    daily = vol_annual / np.sqrt(252.0)
    # stop distance: k units of the volatility over the stop window; ATR when it is given and larger
    stop_pct = s["stop_k"] * daily * np.sqrt(s["stop_window"])
    # ATR is a one-day range; scaling it by the window and multiplying it again by k double-counts for a
    # months-long position (a 30-40% "stop" when this was first run), so ATR only informs the short styles.
    if atr_pct and style != "position":
        stop_pct = max(stop_pct, s["stop_k"] * atr_pct * (np.sqrt(s["stop_window"]) if s["stop_window"] > 1 else 1.0))
    stop_pct = float(min(stop_pct, MAX_STOP))
    risk_inr = risk_per_trade * capital_inr
    by_risk = risk_inr / (stop_pct * price)
    by_weight = max_weight * capital_inr / price
    by_liquidity = PARTICIPATION * adv_inr / price
    shares = int(np.floor(min(by_risk, by_weight, by_liquidity)))
    binding = ("risk budget" if by_risk <= min(by_weight, by_liquidity) else
               "maximum weight" if by_weight <= by_liquidity else "liquidity")
    notional = shares * price
    cm = CostModel()
    rt = cm.round_trip(turnover=max(notional, 1.0), segment=s["segment"], bucket=bucket)
    stop_price = price * (1 - stop_pct) if side == "long" else price * (1 + stop_pct)
    e = EDGE[style]
    out = {
        "ticker": ticker, "style": style, "side": side, "as_of": as_of,
        "edge_status": e["status"], "edge_evidence": e["evidence"], "edge_source": e["source"],
        "shares": shares, "notional": notional, "weight": notional / capital_inr,
        "sized_by": binding, "risk_if_stopped_inr": shares * price * stop_pct,
        "risk_if_stopped_pct_of_account": shares * price * stop_pct / capital_inr,
        "stop_price": round(stop_price, 2), "stop_distance": stop_pct,
        "horizon_sessions": s["horizon"], "review_every_sessions": s["review"],
        "time_exit_on": (_add_sessions(as_of, s["horizon"]) if as_of and s["horizon"] else None),
        "exits": [f"stop: close at or beyond {stop_price:,.2f} ({stop_pct:.1%} {'below' if side == 'long' else 'above'} entry)",
                  f"time: {'flat by 15:15 IST' if style == 'intraday' else str(s['horizon']) + ' sessions after entry'}",
                  f"signal: {s['signal_exit']}", "no profit target (the exit study found targets add no return)"],
        "round_trip_cost": rt["total"], "round_trip_cost_pct": rt["total"] / max(notional, 1.0),
        "breakeven_move_pct": rt["total"] / max(notional, 1.0),
    }
    if shares < 1 or notional < MIN_ORDER_INR:
        out["status"] = "TOO_SMALL"
        out["note"] = (f"the risk budget buys {shares} share(s) = Rs {notional:,.0f}, below the Rs "
                       f"{MIN_ORDER_INR:,.0f} minimum; raise the account size or the risk per trade, or pass")
    elif e["status"] == NONE_DEMONSTRATED:
        out["status"] = "PLAN_WITHOUT_EDGE"
        out["note"] = ("no edge has been demonstrated for this style, so this plan is the arithmetic of "
                       "paying costs; the expected result before luck is a loss equal to the round-trip cost")
    else:
        out["status"] = "PLAN"
        out["note"] = ("a weak, in-sample edge at best; size and stop control the loss, they do not create "
                       "a return")
    out["disclaimer"] = ("Information computed from public statistics, not advice: it knows nothing about "
                         "your finances, needs or tax. Nothing here is sent to a broker.")
    return out


def render(p: dict) -> str:
    lines = [f"PLAN  {p['style'].upper()}  {p['side'].upper()}  {p['ticker']}   [{p['status']}]",
             f"  evidence: {p['edge_status']} - {p['edge_evidence']}",
             f"  size:     {p['shares']} shares = Rs {p['notional']:,.0f} ({p['weight']:.1%} of the account), "
             f"sized by the {p['sized_by']}; loses Rs {p['risk_if_stopped_inr']:,.0f} "
             f"({p['risk_if_stopped_pct_of_account']:.2%} of the account) if the stop is hit",
             f"  horizon:  {p['horizon_sessions']} sessions" + (f", exit by {p['time_exit_on']}" if p['time_exit_on'] else "")
             + (f", review every {p['review_every_sessions']}" if p['review_every_sessions'] else ""),
             "  exits:    " + "; ".join(p["exits"]),
             f"  cost:     round trip Rs {p['round_trip_cost']:,.0f} ({p['round_trip_cost_pct']:.2%}); "
             f"the position must move that far just to break even",
             f"  note:     {p['note']}", f"  {p['disclaimer']}"]
    return "\n".join(lines)
