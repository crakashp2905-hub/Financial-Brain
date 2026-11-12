"""Operational mistakes are mistakes too: an incident ledger, the guard each cause has, and whether the guard is actually on.

Between 6 October and 5 November the live paper session aborted on five mornings because nobody had logged in to Kite, once on a
network failure, and once because a console window was closed - seven sessions of forward evidence lost, each one for a cause
that was visible beforehand. The trading loss review cannot see any of that. This does.

Every incident is one row: when, from which source (the paper runner, the nightly refresh, the pre-open check, the depth archive),
what caused it (a small fixed vocabulary), how bad (a session lost, degraded, or *caught* before it cost anything) and what
detail was recorded. Causes map to **guards** - the control that is meant to stop that cause from costing a session again: when it
was introduced and whether it is *active*. A guard that exists in code but is not scheduled is not a guard, and the report says
so: preflight has existed since 4 November and was still not registered as a task a week later.

Reading, per cause: how many sessions it cost, before and after its guard existed, and one of

    UNGUARDED          no control exists for this cause
    GUARD NOT ACTIVE   a control exists in code but is not running (unscheduled)
    GUARDED            a control exists and is active; recurrences after it are counted and shown
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

SESSION_LOST, DEGRADED, CAUGHT = "SESSION_LOST", "DEGRADED", "CAUGHT"

CAUSE_PATTERNS = (                                    # first match wins; matched on the recorded error text
    ("NO_LOGIN", re.compile(r"no kite access token|kitenotconfigured|token.*expired|tokenexception|invalid.*token", re.I)),
    ("NETWORK", re.compile(r"getaddrinfo|urlopen|timed out|connection(error| reset| refused)|max retries|name resolution", re.I)),
    ("DB_LOCKED", re.compile(r"could not set lock|being used by another process|database is locked|cannot open file", re.I)),
    ("DATA_STALE", re.compile(r"days behind|future-dated|rows lack|contract", re.I)),
    ("DISK_LOW", re.compile(r"gb free|no space left|disk", re.I)),
    ("CONSOLE_KILLED", re.compile(r"killed|keyboardinterrupt|terminated", re.I)),
)


@dataclass(frozen=True)
class Guard:
    name: str
    since: str                                        # the date the control first existed in code
    task: str | None = None                           # the Windows task that must exist for it to run; None: always on
    note: str = ""


GUARDS: dict[str, Guard] = {
    "NO_LOGIN": Guard("pre-open check of the Kite token (fb preflight)", "2026-11-04", "FinanceBrain Preflight"),
    "NETWORK": Guard("pre-open check that Kite and NSE resolve (fb preflight)", "2026-11-04", "FinanceBrain Preflight"),
    "DB_LOCKED": Guard("pre-open check that the research DB opens (fb preflight)", "2026-11-04", "FinanceBrain Preflight"),
    "DATA_STALE": Guard("data contracts, checked pre-open and nightly (fb preflight)", "2026-11-04", "FinanceBrain Preflight"),
    "DISK_LOW": Guard("pre-open free-space check (fb preflight)", "2026-11-04", "FinanceBrain Preflight"),
    "CONSOLE_KILLED": Guard("hidden-window launcher, no console to close (run_hidden.vbs)", "2026-10-07", None),
    "UNSCHEDULED_COLLECTOR": Guard("pre-open check that the depth archive task exists (fb preflight)", "2026-11-12",
                                   "FinanceBrain Preflight"),
}


def classify_text(text: str) -> str:
    for cause, pat in CAUSE_PATTERNS:
        if pat.search(text or ""):
            return cause
    return "CRASH"


def _id(day: str, source: str, cause: str, detail: str) -> str:
    return hashlib.sha1(f"{day}|{source}|{cause}|{detail[:60]}".encode()).hexdigest()[:12]


def _row(day, source, cause, severity, detail, detected_by="") -> dict:
    return {"incident_id": _id(day, source, cause, detail), "day": day, "source": source, "cause": cause,
            "severity": severity, "detail": detail, "detected_by": detected_by}


# ------------------------------------------------------------------------------------------------ collectors

def from_paper_runs(runs: pd.DataFrame, sessions: list[str]) -> list[dict]:
    """The live paper runner's own record: aborted, killed, never-started and unfinished sessions."""
    out: list[dict] = []
    live = runs[runs["mode"] == "live"] if len(runs) else runs
    have = {r.day: r for r in live.itertuples()}
    for day in sessions:
        r = have.get(day)
        if r is None:
            out.append(_row(day, "paper_runner", "NO_RUN", SESSION_LOST, "no live paper session was recorded for an exchange session"))
            continue
        note = str(getattr(r, "note", "") or "")
        if r.status == "aborted":
            out.append(_row(day, "paper_runner", classify_text(note), SESSION_LOST, note or "aborted"))
        elif r.status == "killed":
            out.append(_row(day, "paper_runner", "CONSOLE_KILLED", SESSION_LOST, note or "the run was killed"))
        elif r.status == "running" and day < date.today().isoformat():
            out.append(_row(day, "paper_runner", "CRASH", SESSION_LOST, "the run never recorded a finish"))
    return out


_TB = re.compile(r"Traceback \(most recent call last\):")


def from_refresh_logs(directory: Path) -> list[dict]:
    """The nightly refresh logs: each traceback is a failed step; the last line of it is the reason."""
    out: list[dict] = []
    for p in sorted(directory.glob("refresh-*.log")):
        day = p.stem.removeprefix("refresh-")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        i = 0
        while i < len(lines):
            if _TB.search(lines[i]):
                j = i + 1
                while j < len(lines) and (lines[j].startswith((" ", "\t")) or lines[j].startswith(("Traceback", "File "))):
                    j += 1
                reason = lines[j].strip() if j < len(lines) else lines[-1].strip()
                out.append(_row(day, "nightly_refresh", classify_text(reason), DEGRADED, reason))
                i = j
            i += 1
    return out


_FAIL = re.compile(r"^FAIL\s+(\w+)\s+(.*)$")
_PRE = {"kite": "NO_LOGIN", "network": "NETWORK", "disk": "DISK_LOW", "database": "DB_LOCKED", "contracts": "DATA_STALE",
        "depth_task": "UNSCHEDULED_COLLECTOR"}


def from_preflight_logs(directory: Path) -> list[dict]:
    """Failures the pre-open check *caught*: they cost nothing if fixed before 09:15, and show the guard working."""
    out: list[dict] = []
    for p in sorted(directory.glob("preflight-*.log")):
        day = p.stem.removeprefix("preflight-")
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _FAIL.match(line.strip())
            if m and m.group(1) in _PRE:
                out.append(_row(day, "preflight", _PRE[m.group(1)], CAUGHT, m.group(2), detected_by="preflight"))
    return out


def from_depth_runs(depth_runs: pd.DataFrame) -> list[dict]:
    out = []
    for r in depth_runs.itertuples() if len(depth_runs) else []:
        if r.status in ("no_login", "aborted", "failed"):
            cause = "NO_LOGIN" if r.status == "no_login" else classify_text(str(r.note))
            out.append(_row(r.day, "depth_archive", cause, DEGRADED, str(r.note or r.status)))
    return out


# ------------------------------------------------------------------------------------------------ guards and the report

def _task_registered(name: str) -> bool:
    if sys.platform != "win32":
        return True
    try:
        return subprocess.run(["schtasks", "/Query", "/TN", name], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return True                                   # cannot ask: do not invent a finding


def guard_status(cause: str, *, today: str | None = None, task_registered: Callable[[str], bool] = _task_registered) -> dict:
    g = GUARDS.get(cause)
    today = today or date.today().isoformat()
    if g is None or g.since > today:
        return {"state": "UNGUARDED", "guard": g.name if g else None, "since": g.since if g else None, "active": False}
    active = g.task is None or task_registered(g.task)
    return {"state": "GUARDED" if active else "GUARD NOT ACTIVE", "guard": g.name, "since": g.since, "active": active,
            "task": g.task}


def report(incidents: pd.DataFrame, *, sessions: list[str], today: str | None = None,
           task_registered: Callable[[str], bool] = _task_registered) -> dict:
    """Per cause: sessions lost, before and after the guard existed, and the guard's state; plus session availability."""
    today = today or date.today().isoformat()
    lost_days = set(incidents.loc[incidents["severity"] == SESSION_LOST, "day"]) if len(incidents) else set()
    avail = {"sessions": len(sessions), "lost": len([s for s in sessions if s in lost_days])}
    avail["completed"] = avail["sessions"] - avail["lost"]
    avail["rate"] = avail["completed"] / avail["sessions"] if avail["sessions"] else None
    causes = []
    for cause in sorted(set(incidents["cause"]) if len(incidents) else []):
        x = incidents[incidents["cause"] == cause]
        st = guard_status(cause, today=today, task_registered=task_registered)
        since = st.get("since")
        lost = x[x["severity"] == SESSION_LOST]
        caught = x[x["severity"] == CAUGHT]
        causes.append({"cause": cause, "incidents": int(len(x)), "sessions_lost": int(lost["day"].nunique()),
                       "caught": int(len(caught)), "first": x["day"].min(), "last": x["day"].max(), **st,
                       "lost_before_guard": int(lost[lost["day"] < since]["day"].nunique()) if since else int(lost["day"].nunique()),
                       "lost_after_guard": int(lost[lost["day"] >= since]["day"].nunique()) if since else 0})
    causes.sort(key=lambda c: (-c["sessions_lost"], c["cause"]))
    return {"availability": avail, "causes": causes}


def open_lessons(rep: dict) -> list[dict]:
    """Causes that cost a session and are not under an active guard: the operational lessons still to be acted on."""
    return [c for c in rep["causes"] if c["sessions_lost"] and c["state"] != "GUARDED"]


def render(rep: dict) -> str:
    a = rep["availability"]
    head = (f"Sessions: {a['completed']} of {a['sessions']} expected were recorded ({a['rate']:.0%}); {a['lost']} lost."
            if a["sessions"] else "Sessions: none expected yet.")
    lines = [head, f"{'cause':22s} {'lost':>5s} {'before':>7s} {'after':>6s} {'caught':>7s}  state"]
    for c in rep["causes"]:
        why = f" - {c['guard']}" + (f" (task '{c['task']}' is not registered)" if c["state"] == "GUARD NOT ACTIVE" else "") \
            if c.get("guard") else ""
        lines.append(f"{c['cause']:22s} {c['sessions_lost']:5d} {c['lost_before_guard']:7d} {c['lost_after_guard']:6d} "
                     f"{c['caught']:7d}  {c['state']}{why}")
    if not rep["causes"]:
        lines.append("no incidents recorded")
    return "\n".join(lines)
