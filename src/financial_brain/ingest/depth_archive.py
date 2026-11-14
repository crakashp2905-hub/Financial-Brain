"""A market-hours depth archive wider than the paper runner's: every F&O stock, its near future, and the index options
around the money - full quotes (five levels of the book, OI, VWAP) once a minute, kept permanently.

Separate from ``quote_archive`` on purpose: that file has one writer (the paper runner, whose 200-name universe is
part of a frozen paper strategy and is not touched). This writes ``data/depth_archive.duckdb`` with the same schema,
so ``quote_archive.bars`` / ``breadth`` read it unchanged. Over months it becomes the spread, depth and impact record
that execution calibration needs and no free source sells, and the order-book and open-interest history an intraday
edge would have to be tested on (``archive_health.readiness`` says when there is enough of it).

It needs the owner's Kite session of the day and runs 09:15-15:30 IST; it never places an order (the provider has no
order endpoint). One full-quote call takes up to 500 instruments; a minute's poll is 2-3 calls, well inside Kite's
quote limit, and it backs off for the rest of the minute when a call fails.

Started by the weekday 09:10 task. If the day's login is not there yet it waits for it (until ``LOGIN_BY``), exactly
as the paper runner does; if no trade has printed by 09:25 it is a holiday and the day's rows are removed; a second
copy refuses to start (one writer, no doubled rows). Every run leaves one row in ``runs`` saying how it ended, so a
gap in the archive can be told apart from a day that was never run (``archive_health``).
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import quote_archive as QA

log = logging.getLogger("depth_archive")

PATH = Path("data") / "depth_archive.duckdb"
IST = timezone(timedelta(hours=5, minutes=30))
POLL = 60
OPEN = "09:15"
HOLIDAY_CHECK = "09:25"                     # no trade printed today by then: no session
LOGIN_BY = "10:30"                          # keep waiting for the day's Kite login until then, then give up
MAX_CONSECUTIVE_FAILURES = 90
LOCK_STALE_S = 16 * 3600
STRIKES_EACH_SIDE = 10
INDEX_OPTIONS = ("NIFTY", "BANKNIFTY")

RUNS_SCHEMA = """CREATE TABLE IF NOT EXISTS runs (day VARCHAR PRIMARY KEY, started VARCHAR, finished VARCHAR,
    status VARCHAR, instruments INTEGER, polls INTEGER, rows BIGINT, failed_polls INTEGER, note VARCHAR)"""


def instruments_for_today(kd, spot: dict[str, float] | None = None) -> list[str]:
    """NSE:<stock> for every F&O underlying, NFO:<near future>, and NFO index options +/- 10 strikes of the money."""
    nfo = kd.instruments("NFO")
    futs: dict[str, dict] = {}
    for x in nfo:
        if x.get("instrument_type") == "FUT" and x.get("name"):
            n = x["name"]
            if n not in futs or str(x["expiry"]) < str(futs[n]["expiry"]):
                futs[n] = x
    out = [f"NFO:{x['tradingsymbol']}" for x in futs.values()]
    out += [f"NSE:{n}" for n in futs if n not in INDEX_OPTIONS and n not in ("FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50")]
    spot = spot or {}
    for idx in INDEX_OPTIONS:
        opts = [x for x in nfo if x.get("name") == idx and x.get("instrument_type") in ("CE", "PE")]
        if not opts:
            continue
        near = min(str(x["expiry"]) for x in opts)
        opts = [x for x in opts if str(x["expiry"]) == near]
        strikes = sorted({float(x["strike"]) for x in opts})
        s = spot.get(idx) or strikes[len(strikes) // 2]
        k = min(range(len(strikes)), key=lambda i: abs(strikes[i] - s))
        keep = set(strikes[max(0, k - STRIKES_EACH_SIDE): k + STRIKES_EACH_SIDE + 1])
        out += [f"NFO:{x['tradingsymbol']}" for x in opts if float(x["strike"]) in keep]
    return out


class AlreadyRunning(RuntimeError):
    pass


class _Lock:
    """One writer per archive: an exclusive lock file, taken over only when it is clearly stale."""

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} {datetime.now().isoformat()}".encode())
                os.close(fd)
                return self
            except FileExistsError:
                if time.time() - self.path.stat().st_mtime > LOCK_STALE_S:
                    self.path.unlink(missing_ok=True)
                    continue
                raise AlreadyRunning(f"{self.path} is held: another depth archive is running") from None
        raise AlreadyRunning(f"{self.path} could not be taken")

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def _record(con, day: str, started: str, status: str, **kw) -> None:
    con.execute(RUNS_SCHEMA)
    con.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
                [day, started, datetime.now(IST).isoformat(timespec="seconds"), status, kw.get("instruments", 0),
                 kw.get("polls", 0), kw.get("rows", 0), kw.get("failed_polls", 0), kw.get("note", "")])


def _wait_for_open_and_login(kd, now_fn, sleep, *, stop, t_open, t_login, login_by) -> dict | None:
    """Block until the day's login exists and the market is open. Returns a result dict to stop with, else None."""
    while True:
        now = now_fn()
        if now.time() >= stop:
            return {"status": "failed", "note": "started after the close"}
        try:
            kd.credentials()
        except (kd.KiteNotConfigured, kd.KiteAuthError):
            if now.time() >= t_login:
                return {"status": "no_login", "note": "no Kite login for the day by " + login_by}
            log.warning("no Kite login yet; waiting (scripts\\kite_login.cmd)")
            sleep(60)
            continue
        if now.time() >= t_open:
            return None
        opens_at = datetime.combine(now.date(), t_open, tzinfo=now.tzinfo)
        sleep(min(60.0, max(1.0, (opens_at - now).total_seconds())))


def run(*, until: str = "15:31", poll: int = POLL, now_fn=None, sleep=time.sleep, kd=None, path: Path | None = None,
        login_by: str = LOGIN_BY) -> dict:
    """One session. Returns ``{"status": complete | closed | no_login | aborted | failed, ...}``; never raises on a
    Kite problem (the caller is a scheduled task and the reason belongs in the log and in ``runs``)."""
    if kd is None:
        from ..providers import kite_data as kd
    path = path or PATH
    now_fn = now_fn or (lambda: datetime.now(IST))
    stop = datetime.strptime(until, "%H:%M").time()
    t_open = datetime.strptime(OPEN, "%H:%M").time()
    t_login = datetime.strptime(login_by, "%H:%M").time()
    t_holiday = datetime.strptime(HOLIDAY_CHECK, "%H:%M").time()
    with _Lock(path.with_suffix(".lock")):
        con = QA.connect(path)
        started = now_fn().isoformat(timespec="seconds")
        day = now_fn().date().isoformat()
        polls = rows = fails = consecutive = 0
        syms: list[str] = []
        seen_today = False
        out: dict = {"day": day}
        try:
            stopped = _wait_for_open_and_login(kd, now_fn, sleep, stop=stop, t_open=t_open, t_login=t_login,
                                               login_by=login_by)
            if stopped:
                out.update(stopped)
                return out
            spot = {}
            try:
                q = kd.quote(["NSE:NIFTY 50", "NSE:NIFTY BANK"], kind="ltp")
                spot = {"NIFTY": q.get("NSE:NIFTY 50", {}).get("last_price"),
                        "BANKNIFTY": q.get("NSE:NIFTY BANK", {}).get("last_price")}
            except Exception:                           # noqa: BLE001 - strikes then centre on the middle of the chain
                pass
            for attempt in range(5):
                try:
                    syms = instruments_for_today(kd, spot)
                    break
                except Exception as e:                  # noqa: BLE001 - the instrument list is a plain download: retry
                    log.warning("instrument list failed (%s): %s", attempt + 1, str(e)[:120])
                    sleep(30)
            if not syms:
                out.update(status="failed", note="no instruments")
                return out
            while now_fn().time() < stop:
                t0 = now_fn()
                q = {}
                try:
                    for i in range(0, len(syms), 450):
                        q.update(kd.quote(syms[i:i + 450], kind="full"))
                    consecutive = 0
                except Exception as e:                  # noqa: BLE001 - a failed minute is skipped, not retried
                    fails += 1
                    consecutive += 1
                    log.warning("poll failed (%s in a row): %s", consecutive, str(e)[:120])
                    if consecutive >= MAX_CONSECUTIVE_FAILURES:
                        out.update(status="aborted", note=f"quotes failing: {str(e)[:120]}")
                        return out
                    sleep(max(1.0, poll - (now_fn() - t0).total_seconds()))
                    continue
                if not seen_today:
                    seen_today = any(str(v.get("last_trade_time") or "").startswith(day) for v in q.values())
                if not seen_today and t0.time() >= t_holiday:
                    con.execute("DELETE FROM quotes WHERE CAST(ts AS DATE) = ?", [day])
                    out.update(status="closed", note="no trade printed today: holiday or no session")
                    return out
                rows += QA.store(con, t0.replace(tzinfo=None), q)
                polls += 1
                sleep(max(1.0, poll - (now_fn() - t0).total_seconds()))
            out.update(status="complete")
            return out
        finally:
            out.update(instruments=len(syms), polls=polls, rows=rows, failed_polls=fails)
            try:
                _record(con, day, started, out.get("status", "failed"), instruments=len(syms), polls=polls,
                        rows=rows, failed_polls=fails, note=out.get("note", ""))
            finally:
                con.close()
