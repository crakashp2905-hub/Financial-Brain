"""Where a paper trade's money went: the signal, the execution, the charges.

    signal P&L      what the trade would have made at the prices that triggered entry and exit
  - execution loss  crossing the spread and filling away from those prices (implementation shortfall)
  = gross P&L       what the fills made
  - charges         brokerage, STT, exchange, GST, stamp
  = net P&L

A strategy can be right about direction and still lose to execution; this is where that shows. Replayed trades fill
at an assumed half-spread by liquidity bucket; live trades fill at the quoted bid or ask.
"""
from __future__ import annotations


def attribution(con, *, mode: str | None = None) -> list[dict]:
    where = "WHERE exit_ts IS NOT NULL AND entry_decision_px IS NOT NULL AND exit_decision_px IS NOT NULL"
    params = []
    if mode:
        where += " AND mode = ?"
        params.append(mode)
    rows = con.execute(f"""
        SELECT strategy, mode, COUNT(*) AS trades, SUM(entry_px * qty) AS notional,
               SUM(CASE WHEN side = 'long' THEN exit_decision_px - entry_decision_px
                        ELSE entry_decision_px - exit_decision_px END * qty) AS signal_pnl,
               SUM(gross_pnl) AS gross, SUM(costs) AS charges, SUM(net_pnl) AS net
        FROM trades {where} GROUP BY 1, 2 ORDER BY 1, 2""", params).fetchall()
    out = []
    for s, m, n, notional, sig, gross, charges, net in rows:
        bp = lambda x: x / notional * 1e4 if notional else float("nan")  # noqa: B023, E731
        out.append({"strategy": s, "mode": m, "trades": n, "signal_inr": sig, "execution_inr": gross - sig,
                    "gross_inr": gross, "charges_inr": -charges, "net_inr": net,
                    "signal_bp": bp(sig), "execution_bp": bp(gross - sig), "charges_bp": bp(-charges), "net_bp": bp(net)})
    return out


def render(rows: list[dict]) -> str:
    lines = [f"{'strategy':10s} {'mode':7s} {'trades':>6s} {'signal':>10s} {'execution':>10s} {'charges':>10s} {'net':>10s}   (Rs; bp of notional below)"]
    for r in rows:
        lines.append(f"{r['strategy']:10s} {r['mode']:7s} {r['trades']:6d} {r['signal_inr']:10,.0f} {r['execution_inr']:10,.0f} "
                     f"{r['charges_inr']:10,.0f} {r['net_inr']:10,.0f}")
        lines.append(f"{'':25s} {r['signal_bp']:+9.1f} {r['execution_bp']:+10.1f} {r['charges_bp']:+10.1f} {r['net_bp']:+10.1f}")
    return "\n".join(lines)


def shortfall(con, *, mode: str | None = None) -> list[dict]:
    """Implementation shortfall per strategy, in bp of notional (positive = cost): the fill against the decision
    price (spread and delay), the fill against the day's VWAP at that moment (was it a good time to trade), and what
    walking the visible book would have added for orders larger than the best level (live days only)."""
    where = "WHERE exit_ts IS NOT NULL AND entry_decision_px IS NOT NULL"
    params = []
    if mode:
        where += " AND mode = ?"
        params.append(mode)
    rows = con.execute(f"""
        SELECT strategy, mode, COUNT(*),
               AVG(CASE WHEN side = 'long' THEN entry_px / entry_decision_px - 1 ELSE 1 - entry_px / entry_decision_px END) * 1e4,
               AVG(CASE WHEN entry_vwap IS NULL THEN NULL WHEN side = 'long' THEN entry_px / entry_vwap - 1
                        ELSE 1 - entry_px / entry_vwap END) * 1e4,
               AVG(CASE WHEN exit_vwap IS NULL THEN NULL WHEN side = 'long' THEN 1 - exit_px / exit_vwap
                        ELSE exit_px / exit_vwap - 1 END) * 1e4,
               AVG(entry_walk_bp), COUNT(entry_walk_bp)
        FROM trades {where} GROUP BY 1, 2 ORDER BY 1, 2""", params).fetchall()
    return [{"strategy": s, "mode": m, "trades": n, "entry_vs_decision_bp": a, "entry_vs_vwap_bp": b,
             "exit_vs_vwap_bp": c, "book_walk_bp": w, "with_depth": k} for s, m, n, a, b, c, w, k in rows]
