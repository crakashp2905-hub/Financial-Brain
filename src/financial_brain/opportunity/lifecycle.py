"""One opportunity, end to end: found -> taken or rejected (and why) -> sold -> resolved.

The pieces are recorded where they happen - the scanners' ``opportunities`` table in the paper ledger, the books'
``positions`` and ``skips`` in the book database - and this module reads them back as one timeline, keyed by
(day, ticker). Nothing is re-derived: every event is a row some step wrote at the time.

    FOUND      a scanner flagged it (type, horizon, expected net, evidence)
    TAKEN      a paper book bought it (book, rank, entry)
    REJECTED   it was in a book's top list but not taken (already held / no free slot)
    SOLD       the book sold it (stop or time, net)
    RESOLVED   its horizon passed and the realised return was filled in
"""
from __future__ import annotations

import pandas as pd

ORDER = {"FOUND": 0, "REJECTED": 1, "TAKEN": 1, "SOLD": 2, "RESOLVED": 3}


def timeline(ticker: str, day: str, *, ledger=None, book=None) -> list[dict]:
    ev = []
    if ledger is not None:
        try:
            for t, h, net, evid, real in ledger.execute(
                    """SELECT type, horizon, expected_net, evidence, realised FROM opportunities WHERE ticker = ? AND day = ?""",
                    [ticker, day]).fetchall():
                ev.append({"state": "FOUND", "at": day, "what": f"{t} ({h} sessions), expected net {net * 100:+.2f}%, {evid}"})
                if real is not None:
                    ev.append({"state": "RESOLVED", "at": f"{day} + {h} sessions", "what": f"{t}: realised {real * 100:+.2f}%"})
        except Exception:                               # noqa: BLE001 - no scanner record yet
            pass
    if book is not None:
        for v, m, rank, net, why in book.execute("""SELECT version, mode, rank, expected_net, reason FROM skips
                                                    WHERE ticker = ? AND signal_day = ?""", [ticker, day]).fetchall():
            ev.append({"state": "REJECTED", "at": day, "what": f"{v} ({m}) rank {rank}, expected net {net * 100:+.2f}%: {why}"})
        for r in book.execute("""SELECT version, mode, rank, entry_day, entry_px, exit_day, exit_px, exit_reason, cost
                                 FROM positions WHERE ticker = ? AND signal_day = ?""", [ticker, day]).fetchall():
            v, m, rank, ed, ep, xd, xp, why, cost = r
            ev.append({"state": "TAKEN", "at": str(ed or "next close"), "what": f"{v} ({m}) rank {rank}" + (f", bought at {ep:,.2f}" if ep else "")})
            if xd:
                ev.append({"state": "SOLD", "at": str(xd), "what": f"{v}: {why} at {xp:,.2f}, net {(xp / ep - 1 - cost) * 100:+.2f}%"})
    return sorted(ev, key=lambda e: ORDER[e["state"]])


def render(ticker: str, day: str, events: list[dict]) -> str:
    if not events:
        return f"{ticker} {day}: no record (not flagged, taken or rejected that day)"
    return "\n".join([f"{ticker}, signalled {day}"] + [f"  {e['state']:9s} {e['at']:22s} {e['what']}" for e in events])
