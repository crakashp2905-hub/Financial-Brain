"""What the loop has learned, what it is waiting for, and what needs the owner - as text, as a terminal panel and as a weekly digest.

Three questions, in this order:

1. **What needs me?** A confirmed lesson with no rule, a rule that reached PASSING, an approved rule that is decaying, an approval
   about to expire, an operational cause with no active guard. Everything else is information.
2. **Is it learning, honestly?** How many sessions of evidence exist against how many the protocol needs, the lessons by stage, the
   measured false-confirmation rate when one has been run.
3. **Where is the money going, and is the same mistake coming back?** The loss autopsy by cause and the operational incidents by cause,
   before and after their guards.
"""
from __future__ import annotations

from html import escape as _e
from pathlib import Path

import pandas as pd

from . import autopsy as AU
from . import incidents as IN
from . import protocol as P
from .store import Store

STAGES = ("NOMINATED", "CONFIRMED", "REGISTERED", "SHADOW", "ACTIVE", "REFUTED", "EXPIRED", "RETIRED")


def stage_counts(lessons: dict[str, dict]) -> dict[str, int]:
    out = dict.fromkeys(STAGES, 0)
    for x in lessons.values():
        if x["stage"] in out and x.get("cell"):
            out[x["stage"]] += 1
    return out


def needs_you(lessons: dict[str, dict], shadow: list[dict] | None, ops: dict | None, *, problems: list[str] | None = None) -> list[str]:
    items = []
    registered = {x["cell"] for x in lessons.values() if x["stage"] in ("REGISTERED", "SHADOW", "APPROVED", "ACTIVE") and x.get("cell")}
    for lid, x in lessons.items():
        if x["stage"] == "CONFIRMED" and x.get("cell") not in registered:
            d = x.get("decision") or {}
            items.append(f"CONFIRMED lesson {lid}: {x['cell']} ({d.get('contrast_bp', 0):+.0f} bp worse than the rest on {d.get('trades')} fresh "
                         f"trades). Read it, then `fb learn draft {lid}` to write its pre-registration.")
        if x.get("decaying") and x["stage"] in P.STANDING:
            items.append(f"{x['cell']} may have stopped holding (last {P.DEFAULT.decay_window} sessions). Review lesson {lid}.")
    for r in shadow or []:
        if r["status"] == "PASSING":
            items.append(f"Skip rule {r['rule_id']} ({r['cell']}) is PASSING in shadow: v2 would have made Rs "
                         f"{r['v2_net_inr'] - r['v1_net_inr']:,.0f} more over {r['days']} sessions. Decide: `fb learn approve {r['rule_id']}`.")
        if r["status"] == "FAILING":
            items.append(f"Skip rule {r['rule_id']} ({r['cell']}) would have removed winners. Consider `fb learn retire {r['rule_id']}`.")
    for c in IN.open_lessons(ops) if ops else []:
        if c["state"] == "GUARD NOT ACTIVE":
            items.append(f"{c['cause']} cost {c['sessions_lost']} session(s) and its guard ({c['guard']}) exists but is not running: "
                         f"register the '{c.get('task')}' task.")
        elif c["state"] == "UNGUARDED":
            items.append(f"{c['cause']} cost {c['sessions_lost']} session(s) and nothing guards against it yet.")
    items += problems or []
    return items


def _lessons_table(lessons: dict[str, dict]) -> list[str]:
    rows = [x for x in lessons.values() if x.get("cell")]
    if not rows:
        return ["  none yet"]
    out = [f"  {'lesson':10s} {'stage':11s} {'cell':46s} {'evidence'}"]
    for x in sorted(rows, key=lambda x: (x["stage"], x["id"])):
        n, d = x.get("nomination") or {}, x.get("decision") or {}
        ev = (f"found {n.get('mean_bp', 0):+.0f} bp ({n.get('trades')} trades to {n.get('d0')})"
              + (f"; {d['kind'].lower()} on {d.get('trades')} fresh: {d.get('mean_bp', 0):+.0f} bp, p {d.get('p', 0):.3f}" if d else ""))
        out.append(f"  {x['id']:10s} {x['stage']:11s} {x['cell']:46s} {ev}")
    return out


def status_text(store: Store, *, today: str, shadow: list[dict] | None = None, ops: dict | None = None, depth: dict | None = None,
                ab: dict | None = None, problems: list[str] | None = None, calibration: dict | None = None,
                params: P.Params = P.DEFAULT) -> str:
    lessons = store.lessons()
    runs = store.runs()
    auto = store.autopsies()
    lines = [f"LEARNING LOOP  ({today})", ""]
    ask = needs_you(lessons, shadow, ops, problems=problems)
    lines.append("Needs you:" if ask else "Needs you: nothing.")
    lines += [f"  - {a}" for a in ask]
    last = runs.iloc[-1] if len(runs) else None
    lines += ["", "Evidence"]
    if last is None:
        lines.append("  the loop has not run yet (fb learn run, after the close)")
    else:
        need = f"; screening starts at {params.min_days} sessions" if last["sessions"] < params.min_days else ""
        lines.append(f"  {int(last['sessions'])} live sessions, {int(last['trades'])} closed trades as of {last['session']}{need}. "
                     f"Last run {last['run_on']}: {last['status']}.")
    c = stage_counts(lessons)
    lines.append("  lessons: " + ", ".join(f"{k} {v}" for k, v in c.items() if v) if any(c.values()) else "  lessons: none yet "
                 "(a pattern must be found, then confirmed on sessions it had not seen)")
    lines += _lessons_table(lessons)
    if calibration:
        nl = calibration["null"]
        lines.append(f"  measured on simulated no-effect histories: false confirmation {nl['false_confirmation_rate']:.1%} "
                     f"({calibration['draws']} draws; fb learn calibrate)")
    lines += ["", "Where the money goes (every losing trade, by how it lost)"]
    lines.append(AU.render(auto).replace("\n", "\n  ").join(["  ", ""]) if len(auto) else "  no closed trades yet")
    if ops is not None:
        lines += ["", "Operations (lost sessions by cause, and the guard each has)", "  " + IN.render(ops).replace("\n", "\n  ")]
    if shadow:
        from . import shadow as SH
        lines += ["", "Skip rules", "  " + SH.render(shadow).replace("\n", "\n  ")]
    if ab:
        from . import activation as AC
        lines += ["", AC.render_ab(ab)]
    if depth:
        lines += ["", f"Data collection: {depth['ok_sessions']} complete depth/OI sessions of {depth['register_at']} needed to test an "
                      f"order-flow idea ({depth['stage']})."]
    return "\n".join(lines)


def panel_html(store: Store, *, today: str, shadow: list[dict] | None = None, ops: dict | None = None, depth: dict | None = None,
               problems: list[str] | None = None) -> str:
    """The same story for the terminal page. Every value is escaped; every part degrades to a sentence."""
    lessons = store.lessons()
    out = ["<h2>Learning from mistakes</h2>",
           "<div class='note'>A loss pattern becomes a rule only if it is found, then confirmed once on sessions it had not seen, "
           "then pre-registered, then holds in shadow, then a person approves it. Nothing here changes a live rule.</div>"]
    ask = needs_you(lessons, shadow, ops, problems=problems)
    out.append("<p><b>Needs you:</b></p><ul>" + "".join(f"<li>{_e(a)}</li>" for a in ask) + "</ul>" if ask
               else "<p><b>Needs you:</b> nothing.</p>")
    runs = store.runs()
    if len(runs):
        r = runs.iloc[-1]
        out.append(f"<p>{int(r['sessions'])} live sessions and {int(r['trades'])} closed trades analysed as of {_e(str(r['session']))} "
                   f"(run {_e(str(r['run_on']))}: {_e(str(r['status']))}). Lessons by stage: "
                   + (", ".join(f"{k} {v}" for k, v in stage_counts(lessons).items() if v) or "none yet") + ".</p>")
    else:
        out.append("<p>The loop has not run yet.</p>")
    auto = store.autopsies()
    if len(auto) and (auto["net_pnl"] < 0).any():
        lk = AU.leaks(auto)
        out.append("<div class='wrap'><table><tr><th>How trades lose</th><th class='r'>Trades</th><th class='r'>Lost Rs</th>"
                   "<th class='r'>Share</th></tr>" + "".join(
                       f"<tr><td>{_e(x.cause)}</td><td class='r'>{x.trades}</td><td class='r'>{x.lost_inr:,.0f}</td>"
                       f"<td class='r'>{x.share:.0%}</td></tr>" for x in lk.itertuples()) + "</table></div>")
    if ops and ops["causes"]:
        a = ops["availability"]
        out.append(f"<p>Sessions recorded: {a['completed']} of {a['sessions']}. Lost by cause: " + "; ".join(
            f"{_e(c['cause'])} {c['sessions_lost']} ({_e(c['state'].lower())})" for c in ops["causes"] if c["sessions_lost"]) + ".</p>")
    if depth:
        out.append(f"<p>Order-book and open-interest archive: <b>{depth['ok_sessions']}</b> complete sessions of {depth['register_at']} "
                   f"needed ({_e(depth['stage'])}).</p>")
    return "".join(out)


def digest(store: Store, *, week: str, today: str, shadow: list[dict] | None = None, ops: dict | None = None,
           problems: list[str] | None = None) -> dict:
    """A short weekly message: what happened, what leaked, what needs a decision. One alert per ISO week."""
    auto = store.autopsies()
    lessons = store.lessons()
    parts = []
    if len(auto):
        days = sorted(auto["day"].unique())[-5:]
        w = auto[auto["day"].isin(days)]
        parts.append(f"Last {len(days)} sessions: {len(w)} trades, net Rs {w['net_pnl'].sum():,.0f}.")
        lk = AU.leaks(w)
        if len(lk):
            top = lk.iloc[0]
            parts.append(f"Biggest leak: {top['cause']} (Rs {top['lost_inr']:,.0f}, {top['share']:.0%} of losses).")
    c = stage_counts(lessons)
    parts.append("Lessons: " + (", ".join(f"{v} {k.lower()}" for k, v in c.items() if v) or "none confirmed yet") + ".")
    if ops:
        a = ops["availability"]
        if a["sessions"]:
            parts.append(f"Sessions recorded {a['completed']}/{a['sessions']}.")
    ask = needs_you(lessons, shadow, ops, problems=problems)
    parts.append(f"{len(ask)} item(s) need you: " + " | ".join(a.split(".")[0][:90] for a in ask[:3]) if ask else "Nothing needs you.")
    return {"key": f"learn-digest:{week}", "title": f"Learning loop, week {week}", "body": " ".join(parts)[:900]}


def autopsy_frame_for_week(auto: pd.DataFrame, n_days: int = 5) -> pd.DataFrame:
    days = sorted(auto["day"].unique())[-n_days:]
    return auto[auto["day"].isin(days)]


def depth_summary(path: Path = Path("data") / "depth_archive.duckdb") -> dict | None:
    """Readiness of the order-book archive, or None when it has not started or cannot be read right now."""
    try:
        import duckdb

        from ..ingest import archive_health as AH
        if not Path(path).exists():
            return None
        con = duckdb.connect(str(path), read_only=True)
        try:
            cov = AH.coverage_all(con)
        finally:
            con.close()
        return AH.readiness([cov[d] for d in sorted(cov)])
    except Exception:                                   # noqa: BLE001 - locked during the session, or unreadable
        return None


def latest_shadow(store: Store) -> list[dict]:
    """The most recent verdict for each rule, from the ledger."""
    out: dict[str, dict] = {}
    for e in store.events():
        if e["kind"] == "SHADOW_RESULT":
            out[e["payload"]["rule"]] = e["payload"]
    return list(out.values())


def sessions_since(rcon, first_day: str | None) -> list[str]:
    if not first_day:
        return []
    return [str(r[0]) for r in rcon.execute("SELECT DISTINCT business_date FROM eod_prices WHERE business_date >= CAST(? AS DATE) "
                                            "ORDER BY 1", [first_day]).fetchall()]


def ops_report(store: Store, rcon=None, *, today: str) -> dict | None:
    inc = store.incidents()
    auto = store.autopsies()
    first = min([x for x in (inc["day"].min() if len(inc) else None, auto["day"].min() if len(auto) else None) if x], default=None)
    try:
        sessions = sessions_since(rcon, first) if rcon is not None else []
    except Exception:                                   # noqa: BLE001 - the calendar is a convenience here
        sessions = []
    if not sessions and len(auto):
        sessions = sorted(auto["day"].unique())
    return IN.report(inc, sessions=sessions, today=today) if (len(inc) or sessions) else None


def terminal_html(rcon=None, *, store_path: Path | None = None, depth_path: Path = Path("data") / "depth_archive.duckdb",
                  today: str | None = None) -> str:
    """The learning panel for the terminal page; empty string (never an error) if there is nothing to show yet."""
    from datetime import date

    from .store import PATH
    path = store_path or PATH
    if not Path(path).exists():
        return ("<h2>Learning from mistakes</h2><p>The loop has not run yet (<code>fb learn run</code>, after the close). "
                "See <code>docs/LEARNING-LOOP.md</code>.</p>")
    today = today or date.today().isoformat()
    try:
        with Store(path, read_only=True) as st:
            return panel_html(st, today=today, shadow=latest_shadow(st), ops=ops_report(st, rcon, today=today),
                              depth=depth_summary(depth_path))
    except Exception as e:                              # noqa: BLE001 - an extra panel must never break the page
        return f"<h2>Learning from mistakes</h2><p>Not available right now ({_e(str(e)[:80])}).</p>"
