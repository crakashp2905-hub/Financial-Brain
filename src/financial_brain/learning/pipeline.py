"""One night of the learning loop, end to end.

    collect   the closed live trades, the runner's run log, the market regime and the exchange sessions, read-only, after the close
    label     every trade in the vocabulary of ``features``
    autopsy   why each loss happened, from the path it took (``autopsy``)
    incidents operational failures, their causes and guards (``incidents``)
    protocol  nominate, confirm, watch for decay (``protocol``)
    shadow    registered skip rules judged on the unchanged trades (``shadow``)
    report    the alerts worth sending, and a weekly digest

Each stage is independent: one failing is recorded and does not stop the rest. Re-running a night is harmless (events are unique per
lesson, kind and session). ``collect`` is the only function that touches live files; ``process`` takes plain data so the whole loop can
be driven in tests, and in the calibration, with synthetic ledgers.
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from ..paper import intraday as I
from . import autopsy as AU
from . import features as F
from . import incidents as IN
from . import personal as PS
from . import protocol as P
from . import shadow as SH
from .store import Store

LEDGER = Path("data") / "paper_live.duckdb"
ARCHIVE = Path("data") / "quote_archive.duckdb"
REPORTS = Path("data") / "paper_reports"


@dataclass
class Inputs:
    trades: pd.DataFrame                              # closed live trades, ledger columns
    runs: pd.DataFrame                                # the runner's run log
    regimes: pd.DataFrame                             # business_date, regime, vix
    sessions: list[str]                               # exchange sessions since the first live run
    archive: object | None = None                     # an open quote-archive connection, or None
    refresh_dir: Path | None = None
    depth_runs: pd.DataFrame = field(default_factory=pd.DataFrame)
    problems: list[str] = field(default_factory=list)
    personal: dict | None = None                      # the owner's own habit review (``personal.summary``)


class LedgerBusy(RuntimeError):
    pass


def collect(*, ledger: Path = LEDGER, archive: Path = ARCHIVE, research_db: Path | None = None, reports: Path = REPORTS,
            depth_db: Path | None = Path("data") / "depth_archive.duckdb") -> Inputs:
    """Read what the loop needs. Raises ``LedgerBusy`` when the live runner holds the paper ledger (run after 15:30)."""
    problems: list[str] = []
    try:
        con = duckdb.connect(str(ledger), read_only=True)
    except (duckdb.IOException, duckdb.ConnectionException) as e:        # held by the live run (another process, or this one)
        raise LedgerBusy("the paper ledger is open in the live run; try again after the close") from e
    try:
        trades = con.execute("""SELECT * FROM trades WHERE exit_ts IS NOT NULL AND mode = 'live'""").df()
        runs = con.execute("SELECT day, mode, status, symbols, polls, note FROM runs").df()
    finally:
        con.close()
    regimes, sessions = pd.DataFrame(columns=["business_date", "regime", "vix"]), []
    if research_db is not None:
        try:
            r = duckdb.connect(str(research_db), read_only=True)
            try:
                regimes = r.execute("""SELECT CAST(business_date AS VARCHAR) AS business_date, regime, vix FROM market_regime
                                       WHERE version = 'v2' ORDER BY business_date""").df()
                first = str(runs.loc[runs["mode"] == "live", "day"].min()) if (runs["mode"] == "live").any() else None
                if first:
                    sessions = [str(x[0]) for x in r.execute(
                        "SELECT DISTINCT business_date FROM eod_prices WHERE business_date >= CAST(? AS DATE) ORDER BY 1",
                        [first]).fetchall()]
            finally:
                r.close()
        except Exception as e:                          # noqa: BLE001 - the loop degrades: no regime features, no NO_RUN incidents
            problems.append(f"research database unavailable: {str(e)[:100]}")
    arch = None
    if archive and Path(archive).exists():
        try:
            arch = duckdb.connect(str(archive), read_only=True)
        except Exception as e:                          # noqa: BLE001 - autopsies then lack paths and say so
            problems.append(f"quote archive unavailable: {str(e)[:100]}")
    depth_runs = pd.DataFrame()
    if depth_db and Path(depth_db).exists():
        try:
            d = duckdb.connect(str(depth_db), read_only=True)
            try:
                depth_runs = d.execute("SELECT day, status, note FROM runs").df()
            finally:
                d.close()
        except Exception:                               # noqa: BLE001 - an archive from before run logging, or a locked one
            pass
    return Inputs(trades, runs, regimes, sessions, arch, reports, depth_runs, problems,
                  personal=PS.summary(research_db=research_db))


def day_context(regimes: pd.DataFrame) -> dict[str, dict]:
    """Each session's *previous* session's regime label and VIX: what was known before that day's open."""
    if regimes is None or regimes.empty:
        return {}
    r = regimes.sort_values("business_date").reset_index(drop=True)
    return {str(d): {"regime_prev": prev_r, "vix": prev_v}
            for d, prev_r, prev_v in zip(r["business_date"].iloc[1:], r["regime"].iloc[:-1], r["vix"].iloc[:-1], strict=True)}


def _stage(report: dict, name: str, fn):
    try:
        report["stages"][name] = {"ok": True, **(fn() or {})}
    except Exception as e:                              # noqa: BLE001 - one stage failing must not stop the rest
        report["stages"][name] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        report["trace"][name] = traceback.format_exc()[-1200:]


def process(store: Store, inp: Inputs, *, today: str | None = None, rules_path: Path = SH.RULES_FILE, params: P.Params = P.DEFAULT,
            audit=None, task_registered=IN._task_registered, repo: Path = Path(".")) -> dict:
    """The whole night, on plain data. Returns a report with a stage result for each stage and the alerts to send."""
    today = today or date.today().isoformat()
    report: dict = {"today": today, "stages": {}, "trace": {}, "alerts": [], "problems": list(inp.problems)}
    state: dict = {}

    def label():
        t = inp.trades
        state["trades"] = F.label(t, day_context(inp.regimes)) if len(t) else t
        live_done = set(inp.runs.loc[(inp.runs["mode"] == "live") & (inp.runs["status"] == "complete"), "day"]) if len(inp.runs) else set()
        state["sessions"] = sorted(live_done | set(t["day"])) if len(t) else sorted(live_done)
        return {"trades": int(len(t)), "sessions": len(state["sessions"])}

    def autopsy():
        t = inp.trades
        if t.empty:
            return {"autopsies": 0}
        a = AU.build(t, inp.archive)
        store.replace_autopsies(a)
        return {"autopsies": int(len(a)), "with_path": float(a["has_path"].mean())}

    def incidents():
        rows = IN.from_paper_runs(inp.runs, inp.sessions) if len(inp.runs) else []
        if inp.refresh_dir and Path(inp.refresh_dir).exists():
            rows += IN.from_refresh_logs(Path(inp.refresh_dir)) + IN.from_preflight_logs(Path(inp.refresh_dir))
        rows += IN.from_depth_runs(inp.depth_runs)
        new = store.add_incidents(rows)
        report["incident_rows"] = rows
        rep = IN.report(store.incidents(), sessions=inp.sessions, today=today, task_registered=task_registered)
        report["ops"] = rep
        for row in rows:                               # the newest session lost is worth saying once
            if row["severity"] == IN.SESSION_LOST and inp.sessions and row["day"] == inp.sessions[-1]:
                report["alerts"].append({"key": f"learn-incident:{row['day']}:{row['cause']}",
                                         "title": f"Paper session lost: {row['cause']}",
                                         "body": f"{row['day']}: {row['detail'][:200]}. Guard: "
                                                 f"{IN.guard_status(row['cause'], today=today, task_registered=task_registered)['state']}."})
        return {"incidents": int(len(rows)), "new": new, "open_lessons": len(IN.open_lessons(rep))}

    def protocol():
        sessions, trades = state["sessions"], state["trades"]
        if len(sessions) < params.min_days or trades.empty:
            return {"status": "COLLECTING", "need": params.min_days, "have": len(sessions)}
        cm = P.CellMatrix(trades, sessions)
        store.replace_watchlist(today, P.watchlist(cm, sessions[-1]))
        events = P.step(cm, trades, sessions[-1], store.lessons(), params)
        added = {"NOMINATED": 0, "CONFIRMED": 0, "REFUTED": 0, "EXPIRED": 0, "DECAYING": 0}
        lessons_before = store.lessons()
        for e in events:
            if store.append(e["lesson_id"], e["kind"], session=e["session"], payload=e["payload"]):
                added[e["kind"]] = added.get(e["kind"], 0) + 1
                cell = e["payload"].get("cell") or (lessons_before.get(e["lesson_id"]) or {}).get("cell")
                if e["kind"] == "CONFIRMED":
                    report["alerts"].append({"key": f"learn-confirmed:{e['lesson_id']}", "title": "A loss pattern repeated on new sessions",
                                             "body": f"{cell} is worse than the rest and loses, on sessions it was not found on. "
                                                     f"Nothing changed. Decide: fb learn status, then fb learn draft {e['lesson_id']}."})
                elif e["kind"] == "DECAYING":
                    report["alerts"].append({"key": f"learn-decay:{e['lesson_id']}:{e['session']}", "title": "A confirmed lesson may have stopped holding",
                                             "body": f"{cell}: the last {params.decay_window} sessions no longer show the loss. Review it."})
        return {"status": "RAN", "session": sessions[-1], **added}

    def shadow():
        rules = SH.load_rules(rules_path, repo=repo, audit=audit)
        live = state["trades"] if len(state["trades"]) else pd.DataFrame()
        if len(live) and "notional" in live:
            live = live.copy()
        res = SH.evaluate(live if len(live) else pd.DataFrame(columns=["day"]), rules, today=today,
                          cap_inr=I.MAX_GROSS * I.ACCOUNT_INR)
        changed = SH.record(store, rules, res, state["sessions"][-1] if state.get("sessions") else today)
        report["alerts"] += SH.alerts(res, changed)
        report["shadow"] = res
        problems = [f"{r.id}: {r.problem}" for r in rules if r.problem]
        report["problems"] += problems
        return {"rules": len(rules), "changed": len(changed), "problems": problems}

    def personal():
        s = inp.personal if inp.personal is not None else PS.EMPTY
        report["personal"] = s
        report["alerts"] += PS.alerts(s)
        return {"trips": s.get("trips", 0), "habits_to_avoid": len(s.get("avoid", []))}

    _stage(report, "label", label)
    if report["stages"]["label"]["ok"]:
        _stage(report, "autopsy", autopsy)
    _stage(report, "personal", personal)
    _stage(report, "incidents", incidents)
    if report["stages"]["label"]["ok"]:
        _stage(report, "protocol", protocol)
        _stage(report, "shadow", shadow)
    p = report["stages"].get("protocol", {})
    store.record_run(run_on=today, session=state.get("sessions", [""])[-1] if state.get("sessions") else "",
                     sessions=len(state.get("sessions", [])), trades=int(len(inp.trades)), nominated=p.get("NOMINATED", 0),
                     confirmed=p.get("CONFIRMED", 0), refuted=p.get("REFUTED", 0), decaying=p.get("DECAYING", 0),
                     status="ok" if all(s["ok"] for s in report["stages"].values()) else "partial",
                     note="; ".join(f"{k}: {v['error']}" for k, v in report["stages"].items() if not v["ok"])[:300])
    report["ok"] = all(s["ok"] for s in report["stages"].values())
    return report


def weekly(today: str | None = None) -> bool:
    """The digest goes out on Fridays (the nightly job runs after the close)."""
    d = date.fromisoformat(today) if today else date.today()
    return d.weekday() == 4


def iso_week(today: str | None = None) -> str:
    d = date.fromisoformat(today) if today else date.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
