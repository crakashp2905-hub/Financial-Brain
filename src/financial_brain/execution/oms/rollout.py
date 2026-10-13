"""Rollout stages (ADR-0005): what may trade, how much, and who decides to go further.

    0 OFF          paper only; KiteBroker sends nothing
    1 TICKET       every order waits for a human approval; total deployed <= Rs 50,000
    2 SMALL_AUTO   orders go automatically; total deployed <= Rs 50,000, one order <= Rs 10,000
    3 SCALED       automatic, with limits the owner writes into the stage file

**Up is a human's decision, and only one step at a time.** ``promote`` refuses an ``agent:`` actor, refuses a jump,
and refuses while any criterion for the next stage is unmet - the criteria are below, fixed before the first stage
starts, and the evidence each needs is gathered by the engine, not asserted by the caller.

**Down is automatic.** ``demote`` drops one stage on any failure trigger and trips the kill switch; the engine calls
it. A demotion never raises capital, so it needs no one's permission.

Nothing here ever increases capital on its own: limits come from this table or from the owner's stage file.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from .risk_gate import Limits

PATH = Path("data") / "live" / "stage.json"
OFF, TICKET, SMALL_AUTO, SCALED = 0, 1, 2, 3
NAMES = {OFF: "OFF", TICKET: "TICKET", SMALL_AUTO: "SMALL_AUTO", SCALED: "SCALED"}
REQUIRED_VERDICT = {OFF: None, TICKET: "CANDIDATE", SMALL_AUTO: "CONFIRMED", SCALED: "CONFIRMED"}
VERDICT_RANK = {"FAIL": 0, "FAILED": 0, "FAILED_EARLY": 0, "PENDING": 0, "CANDIDATE": 1, "CONFIRMED": 2}

SMALL = Limits(max_order_inr=10_000, max_position_inr=10_000, max_daily_notional_inr=50_000, max_gross_inr=50_000,
               max_orders_per_day=20, max_orders_per_minute=5)
TICKET_LIMITS = Limits(max_order_inr=25_000, max_position_inr=25_000, max_daily_notional_inr=50_000,
                       max_gross_inr=50_000, max_orders_per_day=20, max_orders_per_minute=5)

#: Entry criteria for each stage, fixed 2026-10-13. Each key is checked against ``evidence`` (see ``unmet``).
CRITERIA = {
    TICKET: {"strategy_verdict_at_least": "CANDIDATE",
             "paper_oms_sessions": 10,             # the OMS run end to end against PaperBroker on live quotes
             "journal_chain_ok": True,
             "runbook_read_by_owner": True},
    SMALL_AUTO: {"strategy_verdict_at_least": "CONFIRMED",
                 "sessions_at_stage": 20, "filled_orders_at_stage": 20,
                 "reconciliation_mismatches": 0, "kill_switch_trips_last_10": 0,
                 "median_shortfall_over_model_max": 2.0, "journal_chain_ok": True},
    SCALED: {"strategy_verdict_at_least": "CONFIRMED",
             "sessions_at_stage": 60, "filled_orders_at_stage": 60,
             "reconciliation_mismatches": 0, "kill_switch_trips_last_10": 0,
             "median_shortfall_over_model_max": 1.5, "live_inside_backtest_band": True, "journal_chain_ok": True},
}

#: Failure triggers at any stage >= TICKET. Each drops one stage and trips the kill switch.
DEMOTE_ON = ("reconciliation mismatch", "kill switch tripped", "shortfall over 3x the model across the last 20 fills",
             "a session's loss above 5% of the stage's capital", "strategy verdict fell below the stage's requirement",
             "journal hash chain broken")


class RolloutError(PermissionError):
    pass


def load(path: Path | None = None) -> dict:
    p = Path(path or PATH)
    if not p.exists():
        return {"stage": OFF, "history": []}
    return json.loads(p.read_text(encoding="utf-8"))


def _write(st: dict, path: Path | None) -> None:
    p = Path(path or PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, default=str), encoding="utf-8")
    tmp.replace(p)


def eligible(stage: int, verdict: str | None) -> bool:
    need = REQUIRED_VERDICT.get(stage)
    return need is None or VERDICT_RANK.get(verdict or "", 0) >= VERDICT_RANK[need]


def limits(st: dict) -> Limits | None:
    s = st["stage"]
    if s == OFF:
        return None
    if s == TICKET:
        return TICKET_LIMITS
    if s == SMALL_AUTO:
        return SMALL
    own = st.get("limits")
    if not own:
        raise RolloutError("stage SCALED needs limits written by the owner into the stage file")
    return Limits(**{k: v for k, v in own.items() if k in Limits.__dataclass_fields__})


def unmet(to: int, evidence: dict) -> list[str]:
    """Every criterion for stage ``to`` that ``evidence`` does not meet. Missing evidence is unmet."""
    out = []
    for k, need in CRITERIA.get(to, {}).items():
        have = evidence.get(k)
        if k == "strategy_verdict_at_least":
            ok = VERDICT_RANK.get(have or "", 0) >= VERDICT_RANK[need]
        elif k.endswith("_max"):
            ok = have is not None and have <= need
        elif isinstance(need, bool):
            ok = have is True
        elif k in ("reconciliation_mismatches", "kill_switch_trips_last_10"):
            ok = have is not None and have <= need
        else:
            ok = have is not None and have >= need
        if not ok:
            out.append(f"{k}: need {need}, have {have}")
    return out


def promote(to: int, actor: str, evidence: dict, *, path: Path | None = None, owner_limits: dict | None = None,
            journal=None) -> dict:
    if not actor or actor.startswith("agent:"):
        raise RolloutError("only a human may raise the rollout stage")
    st = load(path)
    if to != st["stage"] + 1:
        raise RolloutError(f"one stage at a time: {NAMES[st['stage']]} -> {NAMES.get(to, to)} refused")
    missing = unmet(to, evidence)
    if missing:
        raise RolloutError("criteria unmet: " + "; ".join(missing))
    if to == SCALED and not owner_limits:
        raise RolloutError("SCALED needs the owner's limits")
    now = datetime.now(timezone.utc).isoformat()
    st["history"] = st.get("history", []) + [{"from": st["stage"], "to": to, "by": actor, "at": now, "evidence": evidence}]
    st.update(stage=to, since=str(date.today()), set_by=actor, at=now)
    if owner_limits:
        st["limits"] = owner_limits
    _write(st, path)
    if journal is not None:
        journal.note("STAGE_PROMOTED", {"to": NAMES[to], "by": actor, "evidence": evidence}, key=f"stage|{now}")
    return st


def demote(reason: str, *, path: Path | None = None, journal=None, broker=None, ks_path: Path | None = None) -> dict:
    """Automatic: one stage down, and the kill switch tripped with the same reason."""
    from . import killswitch as KS
    st = load(path)
    now = datetime.now(timezone.utc).isoformat()
    new = max(st["stage"] - 1, OFF)
    st["history"] = st.get("history", []) + [{"from": st["stage"], "to": new, "by": "automatic", "at": now,
                                              "reason": reason}]
    st.update(stage=new, since=str(date.today()), set_by="automatic", at=now)
    _write(st, path)
    KS.trip(f"demoted to {NAMES[new]}: {reason}", broker, path=ks_path, journal=journal)
    if journal is not None:
        journal.note("STAGE_DEMOTED", {"to": NAMES[new], "reason": reason}, key=f"demote|{now}")
    return st
