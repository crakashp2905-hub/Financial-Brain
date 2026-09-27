"""Walk drafts toward paper, and say plainly where each one stops (C17 -> C23).

The scorecard can only judge decisions that reached paper, and nothing was moving them.
This does - as far as PAPER_CANDIDATE and no further. Paper is not money, so an agent may
promote to it; ``HUMAN_APPROVED`` remains yours alone, and ``record.advance`` refuses an
agent there regardless of what this module asks for.

The point is as much the refusals as the promotions. A draft that cannot pass evidence
verification, or that the Investment Constitution blocks, stops with the reason attached,
because "3 of 5 drafts were blocked by the concentration rule" is a finding about the
committee, not an error to swallow.
"""
from __future__ import annotations

from datetime import date

from ..paper import ledger as paper
from . import record, safety

TO_PAPER = ("DRAFT", "EVIDENCE_VERIFIED", "RISK_REVIEWED")
TARGET = "PAPER_CANDIDATE"
# A paper trade is only meaningful for a position you could hold.
TRADEABLE = {"BUY", "ADD", "REDUCE", "EXIT", "AVOID"}


def promote(con, did: str, *, actor: str = "agent:promoter") -> dict:
    """Advance one decision as far as paper, or stop at the first rule that refuses."""
    steps: list[str] = []
    while True:
        state = record._state(con, did)
        if state == TARGET or state in record.TERMINAL:
            return {"decision_id": did, "state": state, "steps": steps, "stopped": None}
        if state not in TO_PAPER:
            return {"decision_id": did, "state": state, "steps": steps,
                    "stopped": f"already past paper ({state})"}
        try:
            state = record.advance(con, did, actor=actor, note="promoted toward paper")
            steps.append(state)
        except record.DecisionError as e:
            return {"decision_id": did, "state": record._state(con, did), "steps": steps,
                    "stopped": str(e)}


def run(con, *, actor: str = "agent:promoter", as_of: date | None = None) -> dict:
    """Promote every draft that can go to paper, then open the paper trades."""
    # PAPER_CANDIDATE is included deliberately: a decision that reached paper while the
    # next session's price did not yet exist has no trade, and would otherwise sit there
    # forever because it is already past the states this walks through.
    states = (*TO_PAPER, TARGET)
    live = [r[0] for r in con.execute(f"""
        SELECT e.decision_id FROM (
            SELECT decision_id, to_state, ROW_NUMBER() OVER (PARTITION BY decision_id
                   ORDER BY seq DESC) rk FROM decision_events) e
        LEFT JOIN paper_trades p ON p.decision_id = e.decision_id
        WHERE e.rk = 1 AND e.to_state IN ({','.join('?' * len(states))})
          AND p.decision_id IS NULL""", list(states)).fetchall()]
    out = {"considered": len(live), "reached_paper": 0, "traded": 0, "blocked": [],
           "awaiting_price": 0, "not_tradeable": 0, "unsafe": 0}
    for did in live:
        action = record.content(con, did).get("action")
        if action not in TRADEABLE:
            out["not_tradeable"] += 1
            continue
        result = promote(con, did, actor=actor)
        if result["state"] != TARGET:
            out["blocked"].append({"decision_id": did, "state": result["state"],
                                   "why": result["stopped"]})
            continue
        out["reached_paper"] += 1
        # Situational awareness: stale data, a crisis, a name too thin to exit, a group
        # already held, a run of losses, or a lesson the closed record supports. Every
        # breach is reported, because "thin and in a crisis" is not either one alone.
        context = _context(con, did)
        check = safety.assess(con, isin=record.content(con, did)["isin"],
                              as_of=as_of, **context)
        if not check.safe:
            out["unsafe"] += 1
            out["blocked"].append({"decision_id": did, "state": TARGET,
                                   "why": "unsafe: " + check.describe()})
            continue
        try:
            paper.open_trade(con, did)
            out["traded"] += 1
        except ValueError as e:
            # A trade must enter at a price that existed *after* the decision. Until the
            # next session is ingested there is none - that is waiting, not blocked, and
            # the trade opens by itself on the next run.
            if "no price" in str(e):
                out["awaiting_price"] += 1
            else:
                out["blocked"].append({"decision_id": did, "state": TARGET,
                                       "why": f"no paper trade: {e}"})
        except Exception as e:                # noqa: BLE001 - anything else is a finding
            out["blocked"].append({"decision_id": did, "state": TARGET,
                                   "why": f"no paper trade: {type(e).__name__}: {e}"})
    return out


def _context(con, did: str) -> dict:
    """The features a learned lesson is keyed on, as of now."""
    isin = record.content(con, did)["isin"]
    regime = con.execute("""SELECT regime FROM market_regime
                            ORDER BY business_date DESC, version DESC
                            LIMIT 1""").fetchone()
    event = con.execute("""SELECT event_type FROM announcements WHERE isin = ?
                           AND materiality = 'high' ORDER BY business_date DESC
                           LIMIT 1""", [isin]).fetchone()
    return {"regime": regime[0] if regime else None,
            "prompted_by": event[0] if event else None}
