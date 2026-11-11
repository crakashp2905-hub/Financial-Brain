"""One panel for the terminal page: where the mistakes-to-lessons loop stands and how the intraday data collection is going.

Read-only and defensive: every part degrades to a plain sentence, so a missing table or a locked archive can never break
the page it sits on.
"""
from __future__ import annotations

from html import escape as _e
from pathlib import Path


def _hypotheses(ledger) -> str:
    try:
        rows = ledger.execute("SELECT status, COUNT(*) FROM hypotheses GROUP BY 1 ORDER BY 2 DESC").fetchall()
        confirmed = ledger.execute(
            "SELECT pattern, round(mean_bp, 1), round(t, 2) FROM hypotheses WHERE status = 'CONFIRMED' ORDER BY t").fetchall()
    except Exception:                                   # noqa: BLE001 - no hypotheses table yet
        return "<p>No loss review has run yet.</p>"
    counts = ", ".join(f"{_e(str(s))} {n}" for s, n in rows) or "none yet"
    try:
        run = ledger.execute("""SELECT run_on, status, sessions, groups_examined, flagged, confirmed FROM loss_mining_runs
                                ORDER BY run_on DESC LIMIT 1""").fetchone()
    except Exception:                                   # noqa: BLE001 - the miner has not run
        run = None
    if run is None:
        mining = "The loss miner has not run yet."
    elif run[1] == "INSUFFICIENT":
        mining = (f"The loss miner needs 40 sessions of closed trades and has {run[2]}; it judges nothing until then "
                  "(60% of sessions to find a pattern, 40% to confirm it).")
    else:
        mining = (f"Last mined {_e(str(run[0]))}: {run[2]} sessions, {run[3]} groups examined, {run[4]} flagged, "
                  f"{run[5]} confirmed on later sessions.")
    out = f"<p>Hypotheses by status: {counts}. {mining}</p>"
    if confirmed:
        out += ("<p><b>Repeated out of sample - needs your decision</b> (nothing has been changed): "
                + "; ".join(f"{_e(p)} ({bp:+.1f} bp, t {t:+.2f})" for p, bp, t in confirmed) + "</p>")
    return out


def _shadow(ledger) -> str:
    try:
        rows = ledger.execute("""SELECT rule_id, status, days, skipped_trades, round(v1_net_inr), round(v2_net_inr)
                                 FROM shadow_results WHERE run_on = (SELECT MAX(run_on) FROM shadow_results)""").fetchall()
    except Exception:                                   # noqa: BLE001 - no rule has been registered
        rows = []
    if not rows:
        return "<p>live-v2 shadow: no skip rule is registered, so v2 equals v1. A rule enters only after a confirmed loss is pre-registered.</p>"
    body = "".join(f"<tr><td>{_e(r[0])}</td><td>{_e(r[1])}</td><td class='r'>{r[2]}</td><td class='r'>{r[3]}</td>"
                   f"<td class='r'>{r[4]:,.0f}</td><td class='r'>{r[5]:,.0f}</td></tr>" for r in rows)
    return ("<div class='wrap'><table><tr><th>Skip rule</th><th>Status</th><th class='r'>Sessions</th><th class='r'>Skipped</th>"
            f"<th class='r'>v1 Rs</th><th class='r'>v2 Rs</th></tr>{body}</table></div>")


def _archive(path: Path) -> str:
    try:
        import duckdb

        from ..ingest import archive_health as AH
        if not path.exists():
            return ("<p>Depth and open-interest archive: not started. The weekday 09:10 task "
                    "(<code>scripts\\depth_archive.cmd</code>) has not run; see the runbook.</p>")
        con = duckdb.connect(str(path), read_only=True)
        try:
            cov = AH.coverage_all(con)
        finally:
            con.close()
    except Exception as e:                              # noqa: BLE001 - locked during the session, or unreadable
        return f"<p>Depth archive: not readable right now ({_e(str(e)[:80])}).</p>"
    days = sorted(cov)
    rows = [cov[d] for d in days]
    ready = AH.readiness(rows)
    last = ", ".join(f"{d[5:]} {cov[d]['status']}" for d in days[-5:]) or "no sessions"
    eta = f" About {ready['estimated_ready']} at one complete session a day." if ready["need_to_register"] else ""
    return (f"<p>Depth and open-interest archive: <b>{ready['ok_sessions']} complete session(s)</b> of "
            f"{ready['register_at']} needed before an order-flow or open-interest hypothesis can be registered "
            f"({_e(ready['stage'])}).{eta} Latest: {_e(last)}.</p>")


def html(ledger, *, archive: Path = Path("data") / "depth_archive.duckdb") -> str:
    return ("<h2>Learning from mistakes, and the data an intraday edge would need</h2>"
            "<div class='note'>A loss pattern becomes a rule only if it repeats on sessions it was not found on, survives a "
            "pre-registration, and then holds in shadow beside the unchanged live rules. Nothing here changes a live rule.</div>"
            + _hypotheses(ledger) + _shadow(ledger) + _archive(archive))
