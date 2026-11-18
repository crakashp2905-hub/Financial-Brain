"""Is the intraday archive actually filling, and when will it be enough to test anything on?

Collecting order-book and open-interest history is only worth the disk if the days are complete. This reads
``data/depth_archive.duckdb`` (or any archive with the ``quotes`` table) and answers, per exchange session:

    OK        a full day: enough polls, enough names each poll, the book present
    PARTIAL   something was collected but not a full day (late login, a failed stretch, a crash)
    EMPTY     the session happened and nothing was collected (the task did not run, or no login)
    CLOSED    the run found no trade printed (a holiday the session calendar did not list) - not a gap

The expected sessions come from the research database's own price history (``eod_prices`` dates), so exchange
holidays are never "missing days". ``readiness`` turns the count of OK sessions into the one number that matters for
research: ADR-0004's replication gate needs two halves that each clear |t| >= 2, so an order-flow or open-interest
hypothesis cannot be registered before there are two usable halves of sessions. Alerts follow the same rule as the
preflight: one per distinct failure per day, desktop and (if configured) Telegram.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

POLLS_FULL = 375                  # 09:15-15:30 at one poll a minute
OK_POLL_SHARE = 0.80
OK_MIN_NAMES = 200                # median names per poll: the F&O stocks and their futures are well above this
OK_DEPTH_SHARE = 0.90             # share of rows with a bid and an ask
PARTIAL_MIN_POLLS = 30
GAP_SECONDS = 180                 # a stretch this long without a poll counts as a gap
MIN_SESSIONS_DESCRIPTIVE = 60     # enough to describe spreads and depth by time of day
MIN_SESSIONS_REGISTER = 120       # two halves of 60: the least that lets G3 mean anything (about 6 months)


def _has_quotes(con) -> bool:
    return bool(con.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'quotes'").fetchone()[0])


def _runs(con) -> dict[str, tuple]:
    try:
        return {r[0]: r[1:] for r in con.execute(
            "SELECT day, status, polls, failed_polls, note FROM runs").fetchall()}
    except Exception:                                   # noqa: BLE001 - an archive from before run logging
        return {}


def coverage_all(con) -> dict[str, dict]:
    """Per-day collection quality for every day in the archive, from the rows themselves (works for any run, logged or
    not). Three grouped passes over the table, not one query per day, so it stays cheap as the archive grows."""
    if not _has_quotes(con):
        return {}
    per = con.execute("""
        WITH p AS (SELECT CAST(ts AS DATE) AS d, ts, COUNT(*) AS n,
                          SUM(CASE WHEN bid > 0 AND ask > 0 THEN 1 ELSE 0 END) AS dd FROM quotes GROUP BY 1, 2)
        SELECT d, COUNT(*), median(n), MIN(n), MIN(ts), MAX(ts), SUM(n), SUM(dd) FROM p GROUP BY d""").fetchall()
    gaps = dict(con.execute(f"""
        WITH p AS (SELECT DISTINCT CAST(ts AS DATE) AS d, ts FROM quotes),
             g AS (SELECT d, ts - LAG(ts) OVER (PARTITION BY d ORDER BY ts) AS dt FROM p)
        SELECT d, SUM(CASE WHEN dt > INTERVAL {GAP_SECONDS} SECOND THEN 1 ELSE 0 END) FROM g GROUP BY d""").fetchall())
    fut = {r[0]: (r[1], r[2]) for r in con.execute("""
        SELECT CAST(ts AS DATE), COUNT(DISTINCT CASE WHEN symbol LIKE '%FUT' THEN symbol END),
               COUNT(DISTINCT CASE WHEN symbol LIKE '%FUT' AND oi > 0 THEN symbol END)
        FROM quotes GROUP BY 1""").fetchall()}
    out = {}
    for d, polls, names_med, names_min, first, last, rows, depth in per:
        depth_share = (depth / rows) if rows else 0.0
        ok = (polls >= OK_POLL_SHARE * POLLS_FULL and (names_med or 0) >= OK_MIN_NAMES and depth_share >= OK_DEPTH_SHARE)
        status = "OK" if ok else ("PARTIAL" if polls >= PARTIAL_MIN_POLLS else "EMPTY")
        out[str(d)] = {"day": str(d), "status": status, "polls": int(polls), "names_median": float(names_med or 0),
                       "names_min": int(names_min or 0), "first": str(first), "last": str(last),
                       "gaps": int(gaps.get(d) or 0), "depth_share": round(depth_share, 4), "rows": int(rows),
                       "futures": int(fut.get(d, (0, 0))[0]), "futures_with_oi": int(fut.get(d, (0, 0))[1])}
    return out


def coverage(con, day: date | str) -> dict:
    return coverage_all(con).get(str(day), {"day": str(day), "status": "EMPTY", "polls": 0})


def report(con, sessions: list[date]) -> list[dict]:
    """One row per expected session (newest last), with how the run itself ended when it was logged."""
    runs = _runs(con)
    cov = coverage_all(con)
    out = []
    for s in sessions:
        c = dict(cov.get(str(s), {"day": str(s), "status": "EMPTY", "polls": 0}))
        run = runs.get(str(s))
        if run:
            c["run_status"], c["failed_polls"], c["note"] = run[0], run[2], run[3]
            if c["status"] == "EMPTY" and run[0] == "closed":
                c["status"] = "CLOSED"
        out.append(c)
    return out


def readiness(rows: list[dict], *, history_ok: int = 0) -> dict:
    """How far along the collection is. ``history_ok`` counts OK sessions before the window in ``rows``."""
    ok = history_ok + sum(1 for r in rows if r["status"] == "OK")
    need = max(0, MIN_SESSIONS_REGISTER - ok)
    # about 5 sessions a week, ~250 a year; holidays are not modelled, so this is an estimate and says so
    eta = (datetime.now().date() + timedelta(days=round(need * 7 / 5))) if need else None
    stage = ("READY to register an order-flow or open-interest hypothesis" if not need else
             "enough for a descriptive spread and depth study" if ok >= MIN_SESSIONS_DESCRIPTIVE else
             "collecting; not yet enough to test or describe anything reliably")
    return {"ok_sessions": ok, "need_to_register": need, "estimated_ready": str(eta) if eta else None, "stage": stage,
            "register_at": MIN_SESSIONS_REGISTER, "describe_at": MIN_SESSIONS_DESCRIPTIVE}


def alerts(rows: list[dict], day: date) -> list[dict]:
    """One alert when the newest expected session was not collected, or two sessions in a row were not."""
    if not rows:
        return []
    bad = [r for r in rows[-2:] if r["status"] in ("EMPTY", "PARTIAL")]
    newest = rows[-1]
    if newest["status"] in ("OK", "CLOSED") and len(bad) < 2:
        return []
    last = newest
    why = last.get("note") or ("the task did not run or there was no login" if last["status"] == "EMPTY"
                               else f"{last.get('polls', 0)} polls of about {POLLS_FULL}")
    streak = len(bad)
    return [{"key": f"depth-archive:{day}:{last['day']}:{last['status']}",
             "title": "Depth archive is not filling",
             "body": f"{last['day']} was {last['status']} ({why}); {streak} of the last 2 sessions incomplete. "
                     "Check the 09:10 task and the Kite login (scripts\\kite_login.cmd)."}]


def render(rows: list[dict], ready: dict) -> str:
    lines = [f"{'session':12s} {'status':8s} {'polls':>6s} {'names':>6s} {'gaps':>5s} {'book':>6s} {'fut OI':>8s}  run"]
    for r in rows:
        lines.append(f"{r['day']:12s} {r['status']:8s} {r.get('polls', 0):6d} {r.get('names_median', 0):6.0f} "
                     f"{r.get('gaps', 0):5d} {r.get('depth_share', 0):6.0%} "
                     f"{r.get('futures_with_oi', 0):3d}/{r.get('futures', 0):<4d}  {r.get('run_status', '-')}"
                     f"{(' - ' + r['note']) if r.get('note') else ''}")
    lines.append("")
    lines.append(f"{ready['ok_sessions']} complete session(s); {ready['stage']}. "
                 + (f"Needs {ready['need_to_register']} more (about {ready['estimated_ready']}, holidays not modelled)."
                    if ready["need_to_register"] else ""))
    return "\n".join(lines)
