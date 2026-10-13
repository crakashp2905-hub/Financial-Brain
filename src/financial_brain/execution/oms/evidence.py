"""The evidence a stage promotion is judged on, gathered from the journal and the monitors - never typed in.

Only ``runbook_read_by_owner`` comes from the person promoting, because it is a statement about them.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np

from . import model as M
from . import rollout as RO


def gather(journal, *, strategy: str, verdict: str, runbook_read: bool = False, oos_verdict: str | None = None,
           shortfall_ratios: list[float] | None = None, stage_path=None) -> dict:
    st = RO.load(stage_path)
    since = st.get("since") or "1970-01-01"
    ev = journal.events()
    at_stage = [e for e in ev if e["at"][:10] >= since]
    sessions = {e["at"][:10] for e in at_stage if e["event"] in ("SUBMITTED", "BROKER_UPDATE")}
    rehearsals = {e["payload"].get("day") for e in ev if e["event"] == "REHEARSAL_SESSION"}
    filled = [o for o in journal.orders(states={M.FILLED, M.PARTIAL, M.CANCELLED})
              if o.filled_qty and o.strategy == strategy and o.day >= since]
    ten_sessions_ago = (date.today() - timedelta(days=14)).isoformat()
    trips = [e for e in ev if e["event"] == "KILL_SWITCH_TRIPPED" and e["at"][:10] >= ten_sessions_ago]
    mism = [e for e in at_stage if e["event"] == "RECONCILE_MISMATCH"]
    ratios = [r for r in (shortfall_ratios or []) if r is not None and np.isfinite(r)]
    return {"strategy": strategy, "strategy_verdict_at_least": verdict,
            "paper_oms_sessions": len(rehearsals), "journal_chain_ok": journal.verify()["ok"],
            "runbook_read_by_owner": bool(runbook_read),
            "sessions_at_stage": len(sessions), "filled_orders_at_stage": len(filled),
            "reconciliation_mismatches": len(mism), "kill_switch_trips_last_10": len(trips),
            "median_shortfall_over_model_max": float(np.median(ratios)) if ratios else None,
            "live_inside_backtest_band": oos_verdict in ("PENDING", "CONFIRMED") if oos_verdict else None,
            "gathered_at": datetime.now().isoformat(timespec="seconds")}
