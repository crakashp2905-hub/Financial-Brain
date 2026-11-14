"""Pre-open health check: find what would abort or spoil today's paper session while there is still time to fix it.

Run before the open (``scripts/preflight.cmd``, weekdays 08:30). Most aborted live sessions had a cause that was
visible earlier: no Kite login for the day (five sessions), a locked research database, a failed refresh. This
checks, read-only:

    network     the Kite and NSE hosts resolve
    kite        an access token exists for today (the owner logs in daily; nothing here logs in)
    disk        the data drive has room
    database    the research database opens read-only (no writer holds it)
    contracts   every data contract is met as of the last session (fresh, no future or null dates)
    depth_task  the weekday 09:10 depth and open-interest archive task is registered (its own alert: it does not
                affect today's paper session, but an unregistered task collects nothing and every missed week is lost)

A failure raises one alert per distinct set of failures per day (desktop toast, and Telegram when the owner has
configured it). It cannot know exchange holidays: on a holiday a missing login is expected, so the alert says so.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

HOSTS = ("api.kite.trade", "www.nseindia.com")
MIN_FREE_GB = 50
DEPTH_TASK = "FinanceBrain Depth Archive"
SESSION_CHECKS = ("network", "kite", "disk", "database", "contracts")
RESEARCH_DB = Path("data") / "financial_brain.duckdb"


def _resolve(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


def _credentials() -> None:
    from .providers import kite_data as kd
    kd.credentials()


def _free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / 1e9


def _depth_task_registered() -> bool:
    """Is the 09:10 archive task in the Windows scheduler? Where that cannot be asked, assume yes (no false alarm)."""
    if sys.platform != "win32":
        return True
    try:
        return subprocess.run(["schtasks", "/Query", "/TN", DEPTH_TASK], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return True


def _contracts(db: Path) -> list[dict]:
    import duckdb

    from . import data_contracts
    con = duckdb.connect(str(db), read_only=True)
    try:
        return data_contracts.check(con)
    finally:
        con.close()


def run(*, today: date | None = None, resolve: Callable[[str], bool] = _resolve,
        credentials: Callable[[], object] = _credentials, free_gb: Callable[[Path], float] = _free_gb,
        contracts: Callable[[Path], list[dict]] = _contracts, db: Path = RESEARCH_DB,
        depth_task: Callable[[], bool] = _depth_task_registered) -> list[dict]:
    out: list[dict] = []
    down = [h for h in HOSTS if not resolve(h)]
    out.append({"name": "network", "ok": not down,
                "detail": "all hosts resolve" if not down else f"cannot resolve {', '.join(down)}"})
    try:
        credentials()
        out.append({"name": "kite", "ok": True, "detail": "an access token exists for today"})
    except Exception as e:                              # noqa: BLE001 - any failure to get credentials is the finding
        out.append({"name": "kite", "ok": False, "detail": str(e)[:160]})
    free = free_gb(db.parent if db.parent.exists() else Path("."))
    out.append({"name": "disk", "ok": free >= MIN_FREE_GB, "detail": f"{free:,.0f} GB free (need {MIN_FREE_GB})"})
    try:
        bad = [c for c in contracts(db) if not c["ok"]]
        out.append({"name": "database", "ok": True, "detail": "opens read-only"})
        out.append({"name": "contracts", "ok": not bad,
                    "detail": "all met" if not bad else "; ".join(f"{c['table']}: {c['why']}" for c in bad)[:300]})
    except Exception as e:                              # noqa: BLE001 - a locked or unreadable database is the finding
        out.append({"name": "database", "ok": False, "detail": f"cannot open read-only: {str(e)[:140]}"})
    registered = depth_task()
    out.append({"name": "depth_task", "ok": registered,
                "detail": "the 09:10 depth archive task is registered" if registered else
                "the 09:10 depth archive task is not registered, so no order-book or open-interest history is being "
                "collected; run scripts\\register_depth_archive_task.ps1 once"})
    return out


def alerts(results: list[dict], day: date) -> list[dict]:
    out: list[dict] = []
    failed = [r for r in results if not r["ok"] and r["name"] in SESSION_CHECKS]
    if failed:
        names = ",".join(r["name"] for r in failed)
        body = " | ".join(f"{r['name']}: {r['detail']}" for r in failed)
        out.append({"key": f"preflight:{day}:{names}", "title": "Paper session may not run today",
                    "body": f"{body}. Fix before 09:15 (kite: scripts\\kite_login.cmd). Ignore on an exchange holiday."})
    task = [r for r in results if r["name"] == "depth_task" and not r["ok"]]
    if task:
        out.append({"key": f"preflight:{day}:depth_task", "title": "Depth archive is not scheduled", "body": task[0]["detail"]})
    return out
