"""The control the replay result needs: would buying them all have done as well?

A replayed record of 33% and -1% means little on its own. The committee only ever sees
companies that filed something material that session, and that universe has its own
return - possibly a bad one, possibly a good one. Without measuring it, a positive result
is claimed for the committee when it belonged to the universe, and a negative one is
blamed on the committee when the universe was falling.

So every candidate a session surfaced is recorded, whether or not the committee drafted
it, and each is priced the same way a paper trade is: entry at the first adjusted close
on or after the session, exit at the horizon, excess over the Nifty net of the same
round-trip cost. Then:

    selected    what the committee actually traded
    control     every candidate it was shown, bought blindly

The difference is the committee's contribution. It is the only number here that says
anything about judgement rather than about the market.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..costs.india import CostModel

HORIZON = 90


def _close_on_or_after(con, isin: str, d: date):
    return con.execute("""SELECT a.business_date, a.close_adj FROM adjusted_prices a
                          JOIN security_lineage l ON l.lineage = a.lineage
                          WHERE l.isin = ? AND a.business_date >= ?
                          ORDER BY a.business_date LIMIT 1""", [isin, d]).fetchone()


def _nifty(con, d: date):
    r = con.execute("""SELECT close_level FROM index_levels_canonical
                       WHERE index_name = 'NIFTY 50' AND business_date <= ?
                       ORDER BY business_date DESC LIMIT 1""", [d]).fetchone()
    return float(r[0]) if r else None


def outcome(con, isin: str, on: date, *, horizon: int = HORIZON,
            cost: float = 0.004) -> dict | None:
    """What a blind buy of this company on this session would have returned."""
    entry = _close_on_or_after(con, isin, on)
    if not entry:
        return None
    exit_ = _close_on_or_after(con, isin, entry[0] + timedelta(days=horizon))
    if not exit_ or not entry[1] or float(entry[1]) <= 0:
        return None
    stock = float(exit_[1]) / float(entry[1]) - 1
    n0, n1 = _nifty(con, entry[0]), _nifty(con, exit_[0])
    index = (n1 / n0 - 1) if (n0 and n1) else 0.0
    return {"isin": isin, "entry_date": entry[0], "exit_date": exit_[0],
            "stock_return": stock, "nifty_return": index,
            "excess": stock - index - cost}


def record_candidates(con, d: date, candidates: list[tuple[str, str]],
                      drafted: set[str]) -> None:
    """Remember what a session showed the committee, so the control can be priced later."""
    for isin, company in candidates:
        con.execute("""INSERT INTO replay_candidates (session_date, isin, company,
                       drafted, recorded_at) VALUES (?,?,?,?,NOW())
                       ON CONFLICT (session_date, isin) DO NOTHING""",
                    [d, isin, company, isin in drafted])


def compare(con, *, horizon: int = HORIZON) -> dict:
    """Committee-selected trades against the universe it was shown."""
    cost = CostModel().round_trip(turnover=1_000_000, bucket="mid")["bps"] / 10_000

    selected = [r[0] for r in con.execute("""SELECT excess FROM paper_trades
                                             WHERE status = 'closed'
                                               AND excess IS NOT NULL""").fetchall()]
    control: list[float] = []
    for isin, on in con.execute("""SELECT isin, session_date FROM replay_candidates
                                   ORDER BY session_date""").fetchall():
        got = outcome(con, isin, on, horizon=horizon, cost=cost)
        if got:
            control.append(got["excess"])

    def stat(xs: list[float]) -> dict:
        if not xs:
            return {"n": 0}
        return {"n": len(xs), "mean_excess": sum(xs) / len(xs),
                "hit_rate": sum(1 for x in xs if x > 0) / len(xs),
                "worst": min(xs), "best": max(xs)}

    out = {"selected": stat(selected), "control": stat(control), "cost_charged": cost}
    if selected and control:
        out["contribution"] = (sum(selected) / len(selected)
                               - sum(control) / len(control))
    return out


def lines(result: dict) -> list[str]:
    """The comparison in words, refusing to claim what the samples cannot carry."""
    s, c = result.get("selected", {}), result.get("control", {})
    if not s.get("n") or not c.get("n"):
        return ["Not enough of a record to compare: "
                f"{s.get('n', 0)} selected trades, {c.get('n', 0)} priced candidates."]
    out = [f"Committee traded {s['n']}: hit {s['hit_rate']:.0%}, "
           f"mean excess {s['mean_excess']:+.2%}.",
           f"Same universe bought blindly ({c['n']}): hit {c['hit_rate']:.0%}, "
           f"mean excess {c['mean_excess']:+.2%}.",
           f"Committee contribution: {result['contribution']:+.2%} per trade."]
    if s["n"] < 20:
        out.append("With fewer than 20 selected trades this comparison is directional at "
                   "best - it cannot separate judgement from luck.")
    return out
