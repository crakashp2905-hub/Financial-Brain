"""The exit brain: six views of an open position, one action, and which views are tested.

    thesis      the composite rank now against at entry: bottom 20% -> EXIT (tested: the plan's exit signal);
                fallen by more than 30 points -> REDUCE; risen by 20+ while price is flat -> ADD candidate
    technical   a close below the plan's stop from cost -> EXIT (tested: the stop rule); below the 200-day average ->
                REDUCE
    time        held beyond the horizon -> EXIT (the time exit the studies used)
    risk        volatility expanding (range at its 90th percentile of the year) -> REDUCE
    event       a material filing in the last 2 sessions -> REVIEW (re-read the thesis before acting)
    portfolio   the position above MAX_WEIGHT of the book, or its sector above MAX_SECTOR -> REDUCE

The final action is the most severe view: EXIT > REDUCE > REVIEW > HOLD; ADD only when no view raises more than HOLD. REDUCE sizes are 25% per view that
asks for it, at most 50%. Only the thesis bottom-20%, the stop and the time exit are tested rules; every other view is
a stated rule of thumb, labelled as one - the brain never presents an untested rule as evidence.
"""
from __future__ import annotations

EXIT, REDUCE, REVIEW, HOLD, ADD = "EXIT", "REDUCE", "REVIEW", "HOLD", "ADD"
SEVERITY = {EXIT: 4, REDUCE: 3, REVIEW: 2, HOLD: 1, ADD: 0}
MAX_WEIGHT, MAX_SECTOR = 0.25, 0.50


def decide(*, composite_now: float | None, composite_entry: float | None, price: float, cost: float, stop: float,
           above_ma200: bool | None, held_sessions: int | None, horizon: int, atr_pctile: float | None,
           recent_filing: bool, weight: float, sector_weight: float) -> dict:
    views = []

    def add(view, action, why, tested):
        views.append({"view": view, "action": action, "why": why, "tested": tested})

    # thesis
    if composite_now is not None and composite_now < 0.2:
        add("thesis", EXIT, f"composite rank {composite_now:.0%}: bottom 20%", True)
    elif composite_now is not None and composite_entry is not None and composite_entry - composite_now > 0.30:
        add("thesis", REDUCE, f"composite rank fell from {composite_entry:.0%} to {composite_now:.0%}", False)
    elif (composite_now is not None and composite_entry is not None and composite_now - composite_entry >= 0.20
          and abs(price / cost - 1) < 0.03):
        add("thesis", ADD, f"thesis strengthened ({composite_entry:.0%} -> {composite_now:.0%}) while price is flat", False)
    else:
        add("thesis", HOLD, "composite rank intact" if composite_now is not None else "no rank (illiquid)", False)
    # technical
    if price <= stop:
        add("technical", EXIT, f"below the plan's stop {stop:,.2f}", True)
    elif above_ma200 is False:
        add("technical", REDUCE, "below its 200-day average", False)
    else:
        add("technical", HOLD, "above stop and trend", False)
    # time
    if held_sessions is not None and held_sessions >= horizon:
        add("time", EXIT, f"held {held_sessions} sessions, horizon {horizon}", True)
    else:
        add("time", HOLD, f"{held_sessions if held_sessions is not None else '?'} of {horizon} sessions", True)
    # risk
    if atr_pctile is not None and atr_pctile >= 0.9:
        add("risk", REDUCE, "volatility expanding", False)
    else:
        add("risk", HOLD, "volatility normal", False)
    # event
    add("event", REVIEW if recent_filing else HOLD, "material filing in the last 2 sessions" if recent_filing else "no new filing", False)
    # portfolio
    if weight > MAX_WEIGHT or sector_weight > MAX_SECTOR:
        add("portfolio", REDUCE, f"{weight:.0%} of the book, sector {sector_weight:.0%}", False)
    else:
        add("portfolio", HOLD, "within limits", False)
    final = max(views, key=lambda v: SEVERITY[v["action"]])["action"]
    if final == HOLD and any(v["action"] == ADD for v in views):
        final = ADD                                     # nothing worse than HOLD was raised: the ADD case stands
    reduce_votes = sum(1 for v in views if v["action"] == REDUCE)
    size = 1.0 if final == EXIT else min(0.25 * reduce_votes, 0.5) if final == REDUCE else 0.0
    tested_basis = any(v["tested"] and v["action"] == final for v in views)
    return {"action": final, "reduce_fraction": size, "views": views, "tested_basis": tested_basis}
