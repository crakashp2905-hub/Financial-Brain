"""Reconciliation: what the journal says is held against what the broker says is held.

Run every few minutes while orders are live (``engine.cycle`` does it on each pass) and once after the close. The
journal's positions come only from fills it recorded; the broker's come from its holdings and today's positions.

    match        every (exchange, symbol) agrees
    mismatch     any disagreement -> halt: the kill switch trips and the stage drops one (rollout.demote)

The comparison is limited to symbols the OMS has ever traded (``scope``), because the owner's account holds other
things the OMS does not manage; a holding the OMS never touched is not a mismatch. A symbol the OMS traded that the
broker shows a *different* quantity for is - including when the owner traded it by hand, which the runbook covers:
an account the OMS manages must not be traded by hand in the same symbols.

``baseline`` is the broker quantity in each scoped symbol before the OMS's first order in it, so a position the
owner already had is not mistaken for an OMS fill.
"""
from __future__ import annotations

from datetime import datetime, timezone


def compare(journal_pos: dict, broker_pos: dict, scope: set, baseline: dict | None = None) -> dict:
    baseline = baseline or {}
    diffs = []
    for k in sorted(scope):
        mine = journal_pos.get(k, 0) + baseline.get(k, 0)
        theirs = broker_pos.get(k, 0)
        if mine != theirs:
            diffs.append({"exchange": k[0], "symbol": k[1], "journal": mine, "broker": theirs, "diff": theirs - mine})
    return {"ok": not diffs, "diffs": diffs, "checked": len(scope), "at": datetime.now(timezone.utc).isoformat()}


def run(journal, broker, *, baseline: dict | None = None, halt: bool = True, stage_path=None, ks_path=None) -> dict:
    scope = {(o.exchange, o.symbol) for o in journal.orders()}
    res = compare(journal.positions(), broker.positions(), scope, baseline)
    journal.note("RECONCILED" if res["ok"] else "RECONCILE_MISMATCH", res, key=f"rec|{res['at']}")
    if not res["ok"] and halt:
        from . import rollout as RO
        RO.demote(f"reconciliation mismatch: {res['diffs']}", path=stage_path, journal=journal, broker=broker,
                  ks_path=ks_path)
    return res
