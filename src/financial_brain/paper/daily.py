"""The daily paper-trading loop: decide, remember, resolve, score. Nothing here can place an order.

Run once per session after the data lands. It does four things in order:

1. scan the whole liquid market with :mod:`..opportunity.engine`;
2. write every decision - taken, watched, rejected and why - to opportunity memory under a live run id;
3. resolve every earlier decision whose 20-session window has now closed;
4. report how the closed ones did, **including the rejections**.

The output is a proposed paper book. It is a list of weights for a human to read, not an instruction:
there is no execution code in this module and no path from here to a broker. This is what accumulates
the one thing a backtest cannot supply - decisions made *before* their outcome was known, which is the
only evidence that can eventually satisfy the Money Gate's out-of-sample requirement.
"""
from __future__ import annotations

from datetime import date

from ..opportunity import engine as E
from ..opportunity import memory as M

LIVE_RUN = "live-paper-v1"


def run_daily(con, *, as_of: date | None = None, cfg: E.Config = E.Config(),
              run_id: str = LIVE_RUN) -> dict:
    d = as_of or con.execute("SELECT MAX(business_date) FROM features").fetchone()[0]
    decisions = E.scan(con, d, cfg=cfg)
    M.record(con, run_id=run_id, session=d, signal_from=d, rows=E.to_memory_rows(decisions))
    resolved = M.resolve(con, run_id=run_id, horizon=cfg.horizon)
    book = decisions[decisions["disposition"] == E.TRADE]
    try:
        scorecard = M.report(con, run_id=run_id)
    except M.MemoryError_:
        # Nothing has matured yet: the first 20 sessions of a live run have no outcome to score.
        scorecard = {"resolved_decisions": 0,
                     "why": "no live decision has reached its 20-session horizon yet"}
    return {
        "as_of": d, "names_considered": int(len(decisions)),
        "by_disposition": {k: int(v) for k, v in decisions["disposition"].value_counts().items()},
        "best_net_alpha": float(decisions["net"].max()),
        "hurdle": cfg.min_net,
        "paper_book": [{"ticker": r.ticker, "lineage": r.lineage, "weight": float(r.weight),
                        "net_alpha_20d": float(r.net), "cost": float(r.cost)}
                       for r in book.itertuples()],
        "nothing_clears": bool(book.empty),
        "resolved": resolved,
        "scorecard": scorecard,
        "note": "paper only: no orders exist in this system's reach",
    }
