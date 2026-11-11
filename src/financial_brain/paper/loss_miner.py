"""The loss miner: a losing situation counts as a lesson only if it repeats in sessions it was not found on.

``loss_review`` already groups the closed intraday paper trades by the context they were taken in and writes the
groups that look bad as PROPOSED hypotheses. That is the first half of "learn from mistakes". On its own it cannot
say which of those is real: examine thirty groups and one or two look bad at t = -2 by chance, and a losing streak is
always a pattern in hindsight (the early ORB record, seven sessions, looked like "ORB loses"; it has since made money).

This is the second half. The sessions are split in time. A group is **flagged** on the early (discovery) sessions when
its per-day net is negative at t <= -3 and it survives Benjamini-Hochberg control across every group examined there.
It is **confirmed** only if, on the later sessions it never saw, it is still negative at t <= -2. Everything else is
recorded as failing confirmation, which is the answer that stops a noise pattern from becoming a rule.

* Nothing here changes a rule. A confirmed hypothesis is a request for a decision: pre-register a skip rule, which then
  runs in shadow (``shadow``) beside the unchanged live rules until the forward record says whether it earns its place.
* It needs ``MIN_SESSIONS`` sessions of trades. Below that it says how many it has and writes nothing.
* The unit is one observation per *day* (trades on one day share one market), as in ``loss_review``.
* It only ever moves a hypothesis between PROPOSED, CONFIRMED and FAILED_CONFIRMATION. REGISTERED, SHADOW and anything
  later is the owner's, and is never overwritten.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
from scipy import stats

from . import loss_review as LR

MIN_SESSIONS = 40                 # 24 to discover on and 16 to confirm on, at the very least
DISCOVERY_SHARE = 0.6
T_DISCOVER = 3.0                  # |t| in the discovery window (the pre-registered thresholds of the withdrawn
T_CONFIRM = 2.0                   # "recurring-loss patterns" note: -3 to find, -2 to confirm)
FDR_Q = 0.10
MINED = ("PROPOSED", "CONFIRMED", "FAILED_CONFIRMATION")

SCHEMA = """
CREATE TABLE IF NOT EXISTS loss_mining_runs (
    run_on VARCHAR PRIMARY KEY, sessions INTEGER, discovery_days INTEGER, confirmation_days INTEGER,
    groups_examined INTEGER, flagged INTEGER, confirmed INTEGER, status VARCHAR, note VARCHAR);
"""


def split_sessions(days: list[str], share: float = DISCOVERY_SHARE) -> tuple[list[str], list[str]]:
    days = sorted(days)
    k = max(1, int(len(days) * share))
    return days[:k], days[k:]


def _bh_reject(p: list[float], q: float = FDR_Q) -> list[bool]:
    m = len(p)
    order = sorted(range(m), key=lambda i: p[i])
    cut = -1
    for rank, i in enumerate(order, 1):
        if p[i] <= q * rank / m:
            cut = rank
    keep = {order[r] for r in range(cut)} if cut > 0 else set()
    return [i in keep for i in range(m)]


def _p_worse(t: float, days: int) -> float:
    return float(stats.t.cdf(t, max(days - 1, 1)))


def mine(trades: pd.DataFrame, *, min_sessions: int = MIN_SESSIONS) -> dict:
    """``trades``: closed intraday trades as returned by ``loss_review.frame``."""
    days = sorted(trades["day"].unique())
    out = {"sessions": len(days), "need": min_sessions, "cells": [], "groups_examined": 0, "flagged": 0, "confirmed": 0}
    if len(days) < min_sessions:
        out.update(status="INSUFFICIENT", note=f"{len(days)} of {min_sessions} sessions: nothing is judged yet")
        return out
    disc_days, conf_days = split_sessions(days)
    d = trades[trades["day"].isin(disc_days)]
    c = trades[trades["day"].isin(conf_days)]
    rd = LR.review(d, t_flag=T_DISCOVER)
    rc = LR.review(c, t_flag=T_CONFIRM)
    table = rd["table"]
    out.update(discovery_days=len(disc_days), confirmation_days=len(conf_days), groups_examined=rd["groups_examined"],
               window={"discovery": [disc_days[0], disc_days[-1]], "confirmation": [conf_days[0], conf_days[-1]]})
    if not len(table):
        out.update(status="NO_GROUPS", note="no group had enough trades and days in the discovery window")
        return out
    p = [_p_worse(r.t, r.days) for r in table.itertuples()]
    reject = _bh_reject(p)
    ctab = rc["table"].set_index("pattern") if len(rc["table"]) else pd.DataFrame()
    cells = []
    for r, pi, rej in zip(table.itertuples(), p, reject, strict=True):
        flagged = bool(r.losing and rej)
        if not flagged:
            continue
        cell = {"pattern": r.pattern, "dimension": r.dimension,
                "discovery": {"trades": r.trades, "days": r.days, "mean_bp": float(r.mean_bp), "t": float(r.t),
                              "p": pi, "loss_share": float(r.loss_share)}}
        if r.pattern in getattr(ctab, "index", []):
            x = ctab.loc[r.pattern]
            cell["confirmation"] = {"trades": int(x["trades"]), "days": int(x["days"]), "mean_bp": float(x["mean_bp"]),
                                    "t": float(x["t"])}
            cell["outcome"] = "CONFIRMED" if bool(x["losing"]) else "FAILED_CONFIRMATION"
        else:
            cell["confirmation"] = None
            cell["outcome"] = "INSUFFICIENT_CONFIRMATION"            # too few trades later: neither confirmed nor refuted
        cells.append(cell)
    out["cells"] = cells
    out["flagged"] = len(cells)
    out["confirmed"] = sum(1 for x in cells if x["outcome"] == "CONFIRMED")
    out["status"] = "MINED"
    out["note"] = (f"{rd['groups_examined']} groups examined in discovery ({rd['chance_note']}); "
                   f"{out['flagged']} flagged, {out['confirmed']} confirmed out of sample")
    return out


def apply(con, result: dict, *, today: str | None = None) -> list[str]:
    """Write the run and move hypotheses between the statuses the miner owns. Returns the patterns that became
    CONFIRMED in this run (the ones worth an alert)."""
    today = today or datetime.now().date().isoformat()
    con.execute(LR.SCHEMA)
    con.execute(SCHEMA)
    con.execute("INSERT OR REPLACE INTO loss_mining_runs VALUES (?,?,?,?,?,?,?,?,?)",
                [today, result["sessions"], result.get("discovery_days", 0), result.get("confirmation_days", 0),
                 result.get("groups_examined", 0), result.get("flagged", 0), result.get("confirmed", 0),
                 result["status"], result.get("note", "")])
    newly = []
    now = datetime.now().isoformat(timespec="seconds")
    for cell in result.get("cells", []):
        outcome = cell["outcome"]
        if outcome == "INSUFFICIENT_CONFIRMATION":
            continue                                       # nothing learned: leave the hypothesis as it is
        old = con.execute("SELECT first_seen, status FROM hypotheses WHERE pattern = ?", [cell["pattern"]]).fetchone()
        if old and old[1] not in MINED:
            continue                                       # REGISTERED / SHADOW / PROMOTED / DISCARDED: the owner's
        disc, conf = cell["discovery"], cell["confirmation"]
        note = (f"mined {today}: discovery {disc['trades']} trades / {disc['days']} days, {disc['mean_bp']:+.1f} bp, "
                f"t {disc['t']:+.2f}; confirmation {conf['trades']} trades / {conf['days']} days, "
                f"{conf['mean_bp']:+.1f} bp, t {conf['t']:+.2f}. "
                + ("Next: pre-register a skip rule and run it in shadow (docs/LEARNING-LOOP.md)."
                   if outcome == "CONFIRMED" else "It did not repeat out of sample; no rule."))
        con.execute("INSERT OR REPLACE INTO hypotheses VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [cell["pattern"], old[0] if old else now, now, conf["trades"], conf["days"], conf["mean_bp"],
                     conf["t"], disc["loss_share"], result["groups_examined"], outcome, note])
        if outcome == "CONFIRMED" and (not old or old[1] != "CONFIRMED"):
            newly.append(cell["pattern"])
    return newly


def alerts(newly: list[str], day) -> list[dict]:
    return [{"key": f"loss-confirmed:{pat}", "title": "A losing situation repeated out of sample",
             "body": f"{pat}. It lost in the early sessions and again in the later ones. Nothing changed; decide whether to "
                     "pre-register a skip rule (docs/LEARNING-LOOP.md)."} for pat in newly]


def render(r: dict) -> str:
    if r["status"] == "INSUFFICIENT":
        return (f"Loss mining: {r['note']}. Each night it re-checks; it starts judging once there are {r['need']} "
                "sessions of closed trades, split 60/40 into a discovery and a confirmation window.")
    lines = [f"Loss mining: {r['sessions']} sessions; discovery {r['window']['discovery'][0]} to "
             f"{r['window']['discovery'][1]} ({r['discovery_days']} days), confirmation {r['window']['confirmation'][0]} to "
             f"{r['window']['confirmation'][1]} ({r['confirmation_days']} days).", r.get("note", "")]
    for c in r["cells"]:
        d, k = c["discovery"], c["confirmation"]
        later = (f"later {k['mean_bp']:+.1f} bp t {k['t']:+.2f} ({k['trades']} trades)" if k else "too few trades later")
        lines.append(f"  {c['outcome']:26s} {c['pattern']:42s} discovery {d['mean_bp']:+.1f} bp t {d['t']:+.2f}; {later}")
    if not r["cells"]:
        lines.append("  no group was flagged in discovery: nothing to confirm")
    return "\n".join(lines)
