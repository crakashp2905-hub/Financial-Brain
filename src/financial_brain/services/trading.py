"""Trading operations: the out-of-sample monitor (ADR-0004 G5) and the order management system (ADR-0005).

These read and write the **side ledgers** - ``data/paper_zoo.duckdb``, ``data/paper_live.duckdb``,
``data/oms.duckdb`` - never the research database, so unlike the other services they open their own files by path:
each ledger has exactly one owner process and a caller has no connection to lend. The research database, where one
is needed, is still opened read-only.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb

ZOO = Path("data") / "paper_zoo.duckdb"
LIVE_LEDGER = Path("data") / "paper_live.duckdb"
LIVE_JOURNAL = Path("data") / "oms.duckdb"
PAPER_JOURNAL = Path("data") / "oms_paper.duckdb"
REHEARSAL_CAPITAL = 50_000.0


# ------------------------------------------------------------------ ADR-0004 G5

def oos(zoo: Path = ZOO, live: Path = LIVE_LEDGER) -> dict:
    from ..evaluation import metalabel as ML
    from ..evaluation import oos_monitor as OM
    z = duckdb.connect(str(zoo), read_only=True)
    try:
        trials = OM.report(z)
    finally:
        z.close()
    try:
        lc = duckdb.connect(str(live), read_only=True)
        try:
            ex, ml = OM.execution_drift(lc), ML.readiness(lc)
        finally:
            lc.close()
    except duckdb.Error as e:                           # the live runner holds it during the session
        ex, ml = {"trades": 0, "note": str(e)}, {"evidence": {"ok": False, "note": str(e)}}
    return {"trials": trials, "execution": ex, "metalabel": ml}


def render_trials(rows: list[dict]) -> str:
    from ..evaluation import oos_monitor as OM
    return OM.render(rows)


def verdict(strategy: str) -> str:
    """The strategy's ADR-0004 standing: CONFIRMED from G5, CANDIDATE from the ledger, else FAIL."""
    from ..evaluation import oos_monitor as OM
    from ..evaluation import standard as S
    try:
        z = duckdb.connect(str(ZOO), read_only=True)
        try:
            for r in OM.report(z):
                if r["strategy"] == strategy and r["verdict"] == OM.CONFIRMED:
                    return "CONFIRMED"
        finally:
            z.close()
    except duckdb.Error:
        pass
    reg = {x.strategy: x.prereg for x in OM.REGISTERED}
    for row in S.ledger():
        if row.get("prereg") == reg.get(strategy) and row.get("verdict") == S.CANDIDATE:
            return "CANDIDATE"
    # ADR-0006 (accepted 2026-10-15): a modest edge that clears the small tier's fixed checks.
    from ..evaluation import small_tier as ST
    try:
        z = duckdb.connect(str(ZOO), read_only=True)
        try:
            if ST.assess(strategy, z)["qualifies"]:
                return ST.CANDIDATE_SMALL
        finally:
            z.close()
    except duckdb.Error:
        pass
    return "FAIL"


# ------------------------------------------------------------------ ADR-0005

def status() -> dict:
    from ..evaluation import oos_monitor as OM
    from ..execution.oms import journal as J
    from ..execution.oms import kite_broker as KB
    from ..execution.oms import killswitch as KS
    from ..execution.oms import rollout as RO
    st = RO.load()
    j = J.Journal(LIVE_JOURNAL)
    try:
        today = [o for o in j.orders() if o.day == str(date.today())]
        out = {"stage": RO.NAMES[st["stage"]], "since": st.get("since"), "kill_switch": KS.load(),
               "journal": j.verify(), "orders_today": [o.as_dict() for o in today],
               "awaiting_approval": [o.client_id for o in today if o.state == "AWAITING_APPROVAL"]}
    finally:
        j.close()
    out["verdicts"] = {s.strategy: verdict(s.strategy) for s in OM.REGISTERED}
    out["locks_closed"] = {s: KB.locks(v) for s, v in out["verdicts"].items()}
    return out


def rehearse(strategy: str) -> dict:
    """The zoo's current targets for ``strategy``, sized to Rs 50,000, worked through the OMS against PaperBroker
    priced by live Kite quotes. Counts toward stage TICKET only inside the session, with quotes."""
    from ..execution.oms import broker as B
    from ..execution.oms import engine as EN
    from ..execution.oms import journal as J
    from ..execution.oms import model as M
    from ..execution.oms import risk_gate as RG
    from ..providers import kite_data as kd
    now = datetime.now(timezone.utc)
    z = duckdb.connect(str(ZOO), read_only=True)
    try:
        last = z.execute("SELECT MAX(day) FROM zoo_targets WHERE strategy = ?", [strategy]).fetchone()[0]
        tg = dict(z.execute("SELECT key, weight FROM zoo_targets WHERE strategy = ? AND day = ?", [strategy, last]).fetchall())
    finally:
        z.close()
    if not tg:
        return {"ok": False, "why": f"no zoo targets for {strategy}"}
    r = duckdb.connect("data/financial_brain.duckdb", read_only=True)
    try:
        names = dict(r.execute("SELECT lineage, ticker FROM adjusted_prices WHERE business_date = "
                               "(SELECT MAX(business_date) FROM adjusted_prices) AND lineage IN (SELECT UNNEST(?))",
                               [list(tg)]).fetchall())
    finally:
        r.close()
    syms = [names[k] for k in tg if k in names and tg[k] > 0]
    quotes = kd.quote([f"NSE:{s}" for s in syms], kind="ltp")
    px = {("NSE", k.split(":", 1)[1]): float(v["last_price"]) for k, v in quotes.items()}
    try:
        banned = RG.fetch_ban_list()
    except Exception:                                   # noqa: BLE001 - an unreadable list refuses every order
        banned = None
    b = B.PaperBroker()
    b.tick(px)
    j = J.Journal(PAPER_JOURNAL)
    try:
        oms = EN.OMS(j, b, verdicts={strategy: verdict(strategy)}, banned=banned,
                     stage_path=Path("data/live/paper_stage.json"), ks_path=Path("data/live/paper_ks.json"))
        day = str(date.today())
        placed = []
        for lin, w in tg.items():
            s = names.get(lin)
            if not s or w <= 0 or ("NSE", s) not in px:
                continue
            p = px[("NSE", s)]
            qty = int(REHEARSAL_CAPITAL * w // p)
            if qty < 1:
                continue
            o = oms.submit(strategy=strategy, day=day, symbol=s, side=M.BUY, qty=qty,
                           limit_price=RG.round_to_tick(p * 1.0005, M.BUY), arrival_price=p)
            placed.append(o.state)
        b.tick(px)
        oms.poll()
        res = oms.end_of_day(day)
    finally:
        j.close()
    ist = now.astimezone(timezone(timedelta(hours=5, minutes=30)))
    counted = ist.weekday() < 5 and RG.OPEN <= ist.time() <= RG.CLOSE and bool(px)
    if counted:
        live = J.Journal(LIVE_JOURNAL)
        try:
            live.note("REHEARSAL_SESSION", {"day": day, "strategy": strategy, "orders": len(placed)},
                      key=f"rehearsal|{day}|{strategy}")
        finally:
            live.close()
    return {"ok": True, "orders": len(placed), "states": {s: placed.count(s) for s in set(placed)}, "cycle": res,
            "counted_toward_stage_1": counted}


def verify() -> dict:
    from ..execution.oms import journal as J
    j = J.Journal(LIVE_JOURNAL)
    try:
        return j.verify()
    finally:
        j.close()


def kill(reason: str, strategy: str) -> dict:
    """Trip the broker kill switch; cancel at Kite when a session exists, trip regardless."""
    from ..execution.oms import journal as J
    from ..execution.oms import killswitch as KS
    broker = None
    try:
        from ..execution.oms.kite_broker import KiteBroker
        broker = KiteBroker(verdict(strategy))
        broker.orders()
    except Exception:                                   # noqa: BLE001 - no session: the switch still trips
        broker = None
    j = J.Journal(LIVE_JOURNAL)
    try:
        return KS.trip(reason, broker, journal=j)
    finally:
        j.close()


def reset(actor: str, note: str) -> dict:
    from ..execution.oms import journal as J
    from ..execution.oms import killswitch as KS
    j = J.Journal(LIVE_JOURNAL)
    try:
        return KS.reset(actor, note, journal=j)
    finally:
        j.close()


def promote(to: int, actor: str, strategy: str, runbook_read: bool) -> dict:
    """Raises ``rollout.RolloutError`` (a PermissionError) with every unmet criterion."""
    from ..execution.oms import evidence as EV
    from ..execution.oms import journal as J
    from ..execution.oms import rollout as RO
    j = J.Journal(LIVE_JOURNAL)
    try:
        ev = EV.gather(j, strategy=strategy, verdict=verdict(strategy), runbook_read=runbook_read)
        return RO.promote(to, actor, ev, journal=j)
    finally:
        j.close()


def ticket(action: str, client_id: str, actor: str, strategy: str) -> dict:
    from ..execution.oms import engine as EN
    from ..execution.oms import journal as J
    from ..execution.oms.kite_broker import KiteBroker
    j = J.Journal(LIVE_JOURNAL)
    try:
        v = verdict(strategy)
        oms = EN.OMS(j, KiteBroker(v), verdicts={strategy: v})
        o = oms.approve(client_id, actor) if action == "approve" else oms.decline(client_id, actor)
        return o.as_dict()
    finally:
        j.close()
