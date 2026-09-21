"""Paper trading (ARCHITECTURE.md §11: validation -> *paper trade* -> promotion).

A decision that reaches PAPER_CANDIDATE is traded on paper, with rules fixed here so no
result can be flattered after the fact:

* **Entry** at the adjusted close of the first session on or after the world state's
  as-of date - never a price the decision could not have had. (Closes only: the lake
  holds no opens, so this is the first price after the pre-open as-of.)
* **Exit** at the close of the first session on or after entry + ``horizon_days``.
* **Costs**: the Indian round trip (STT both legs, charges, impact by liquidity bucket
  from the name's 20-session traded value) is subtracted once.
* **Direction** follows the action: BUY/ADD/HOLD are long (+1); EXIT/REDUCE/AVOID were
  right if the stock fell or lagged (-1); WATCH is recorded but carries no P&L.
* **Benchmark**: Nifty 50 over the same dates. Excess = direction x (stock - Nifty) - cost.

Entry facts never change once written; ``mark`` only fills in the exit of trades whose
due date has passed. Their outcome is what OUTCOME_MEASURED and the postmortem are about.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from ..costs.india import CostModel

DIRECTION = {"BUY": 1, "ADD": 1, "HOLD": 1, "EXIT": -1, "REDUCE": -1, "AVOID": -1,
             "WATCH": 0}
PAPER_STATES = {"PAPER_CANDIDATE", "HUMAN_APPROVED", "PROPOSED_TO_BROKER", "EXECUTED",
                "OUTCOME_MEASURED", "POSTMORTEM_COMPLETE"}


def _bucket(adv: float | None) -> str:
    if adv is None:
        return "micro"
    for name, floor in (("mega", 5e9), ("large", 1e9), ("mid", 2e8), ("small", 5e7)):
        if adv >= floor:
            return name
    return "micro"


def _price_on_or_after(con, isin: str, d: date):
    return con.execute("""SELECT a.business_date, a.close_adj, a.lineage
        FROM adjusted_prices a JOIN security_lineage l ON l.lineage = a.lineage
        WHERE l.isin = ? AND a.business_date >= ? ORDER BY a.business_date LIMIT 1""",
                       [isin, d]).fetchone()


def _nifty(con, d: date):
    r = con.execute("""SELECT close_level FROM index_levels_canonical
                       WHERE index_name = 'Nifty 50' AND business_date <= ?
                       ORDER BY business_date DESC LIMIT 1""", [d]).fetchone()
    return r[0] if r else None


def open_trade(con, decision_id: str) -> dict:
    """Open the paper trade for a decision in (or past) PAPER_CANDIDATE. Idempotent."""
    old = con.execute("SELECT * FROM paper_trades WHERE decision_id = ?",
                      [decision_id]).fetchone()
    if old:
        return {"decision_id": decision_id, "status": "exists"}
    state = con.execute("""SELECT to_state FROM decision_events WHERE decision_id = ?
                           ORDER BY seq DESC LIMIT 1""", [decision_id]).fetchone()
    if not state or state[0] not in PAPER_STATES:
        raise ValueError(f"{decision_id} is {state[0] if state else 'unknown'}; paper "
                         "trading starts at PAPER_CANDIDATE")
    c = json.loads(con.execute("SELECT content FROM decisions WHERE decision_id = ?",
                               [decision_id]).fetchone()[0])
    ws = json.loads(con.execute("SELECT content FROM world_states WHERE version_id = ?",
                                [c["world_state_version"]]).fetchone()[0])
    start = datetime.fromisoformat(str(ws["as_of"])).date()
    px = _price_on_or_after(con, c["isin"], start)
    if not px:
        raise ValueError(f"no price for {c['isin']} on or after {start} yet")
    try:
        adv = con.execute("""SELECT adv20 FROM features WHERE lineage = ?
                             AND business_date <= ? ORDER BY business_date DESC LIMIT 1""",
                          [px[2], px[0]]).fetchone()
    except Exception:          # noqa: BLE001 - no feature history is not a reason to
        adv = None             # refuse the trade; _bucket already handles the unknown
    bucket = _bucket(adv[0] if adv else None)
    cost = CostModel().round_trip(turnover=1_000_000, bucket=bucket)["bps"] / 10_000
    con.execute("""INSERT INTO paper_trades (decision_id, isin, lineage, action, direction,
        weight, entry_date, entry_price, entry_nifty, due_date, cost, bucket, status,
        opened_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'open', ?)""",
                [decision_id, c["isin"], px[2], c["action"], DIRECTION[c["action"]],
                 (c.get("sizing") or {}).get("weight"), px[0], px[1], _nifty(con, px[0]),
                 px[0] + timedelta(days=c["horizon_days"]), cost, bucket,
                 datetime.now(timezone.utc)])
    return {"decision_id": decision_id, "status": "open", "entry_date": px[0],
            "entry_price": px[1], "bucket": bucket, "cost": cost}


def mark(con) -> dict:
    """Close every open trade whose due date has a price. Returns counts."""
    closed = 0
    for did, isin, due, entry_date, entry_nifty, direction, cost in con.execute(
            """SELECT decision_id, isin, due_date, entry_date, entry_nifty, direction, cost
               FROM paper_trades WHERE status = 'open'""").fetchall():
        px = _price_on_or_after(con, isin, due)
        if not px:
            continue
        # Adjusted closes are rebased when a later split is recorded, so the stored entry
        # price may be on an older basis: re-read entry and exit from the same series.
        entry_px = _price_on_or_after(con, isin, entry_date)[1]
        stock = px[1] / entry_px - 1
        nifty_exit = _nifty(con, px[0])
        bench = nifty_exit / entry_nifty - 1 if nifty_exit and entry_nifty else None
        excess = (direction * (stock - (bench or 0)) - (cost if direction else 0))
        con.execute("""UPDATE paper_trades SET status = 'closed', exit_date = ?,
                       exit_price = ?, stock_return = ?, nifty_return = ?, excess = ?,
                       closed_at = ? WHERE decision_id = ?""",
                    [px[0], px[1], stock, bench, excess, datetime.now(timezone.utc), did])
        closed += 1
    still = con.execute("SELECT COUNT(*) FROM paper_trades WHERE status = 'open'").fetchone()[0]
    return {"closed": closed, "open": still}


def scoreboard(con) -> dict:
    r = con.execute("""SELECT COUNT(*), AVG(excess), AVG(CASE WHEN excess > 0 THEN 1.0
                       ELSE 0 END) FROM paper_trades WHERE status = 'closed'
                       AND direction <> 0""").fetchone()
    return {"closed": r[0], "mean_excess": r[1], "hit_rate": r[2]}
