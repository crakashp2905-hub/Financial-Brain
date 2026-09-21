"""Replay the committee over past sessions, so its calls can be scored (C23/C25).

The scorecard cannot judge decisions that were never made. Waiting for a year of live
calls is the honest way to get them and also the slowest, so this replays the machinery
over sessions that have already happened: build the world state **as of that session**,
run the committee on companies that had a material filing that day, walk the draft to
paper, and let ``paper.mark`` close it at its horizon against the prices we hold.

What makes this a replay rather than a fiction:

* the world state is built with that session's ``as_of``, so only filings published by
  then are in it;
* ``record.advance`` refuses evidence published after that ``as_of`` - the decision
  contract already enforces no-hindsight, and this relies on it rather than repeating it;
* the entry price is the first adjusted close **on or after** the decision, and the exit
  is the close at the horizon - the same rule a live trade gets.

What it is still not: a strategy backtest. The universe is "companies that filed
something material that day", the model is today's model reading old text, and the sample
is small. It measures whether this machinery makes money, not whether a strategy does.
"""
from __future__ import annotations

from datetime import date

from ..committee import run as committee
from ..decisions import promote
from ..paper import ledger as paper
from ..worldstate import build as ws

# A committee run costs a minute or two of local inference, so the universe per session
# is deliberately small and chosen by what the session itself surfaced.
CANDIDATE_SQL = """
    SELECT a.isin, MAX(a.company) AS company, COUNT(*) AS filings
    FROM announcements a
    WHERE a.business_date = ? AND a.materiality = 'high' AND a.isin IS NOT NULL
      AND a.event_type IN ('ORDER_WIN', 'RESULTS', 'LEGAL_REGULATORY', 'ACQUISITION',
                           'MANAGEMENT_CHANGE', 'CREDIT_RATING', 'DIVIDEND')
      AND EXISTS (SELECT 1 FROM adjusted_prices p JOIN security_lineage l
                  ON l.lineage = p.lineage
                  WHERE l.isin = a.isin AND p.business_date = ?)
    GROUP BY a.isin ORDER BY filings DESC, a.isin
"""


def candidates(con, d: date, limit: int) -> list[tuple[str, str]]:
    rows = con.execute(CANDIDATE_SQL, [d, d]).fetchall()
    return [(r[0], r[1]) for r in rows[:limit]]


def session(con, d: date, *, per_day: int = 2, model: str | None = None,
            actor: str = "agent:replay") -> dict:
    """One past session: world state, committee, promotion."""
    state = ws.build(con, d)
    out = {"date": d, "world_state": state["version_id"], "considered": 0,
           "drafted": 0, "traded": 0, "notes": []}
    for isin, company in candidates(con, d, per_day):
        out["considered"] += 1
        try:
            res = committee.convene(con, isin, state["version_id"],
                                    **({"model": model} if model else {}))
        except Exception as e:                # noqa: BLE001 - one company, not the run
            out["notes"].append(f"{company}: committee failed: {type(e).__name__}: {e}")
            continue
        if not res.decision_id:
            out["notes"].append(f"{company}: no draft (debate produced no cited points)")
            continue
        out["drafted"] += 1
    moved = promote.run(con, actor=actor)
    out["traded"] = moved["traded"]
    out["blocked"] = moved["blocked"]
    return out


def run(con, dates: list[date], *, per_day: int = 2, model: str | None = None) -> dict:
    """Replay several sessions, then close whatever has reached its horizon."""
    sessions = []
    for d in dates:
        sessions.append(session(con, d, per_day=per_day, model=model))
    marked = paper.mark(con)
    return {"sessions": sessions, "drafted": sum(s["drafted"] for s in sessions),
            "traded": sum(s["traded"] for s in sessions), "closed": marked}
