"""A market-hours depth archive wider than the paper runner's: every F&O stock, its near future, and the index options
around the money - full quotes (five levels of the book, OI, VWAP) once a minute, kept permanently.

Separate from ``quote_archive`` on purpose: that file has one writer (the paper runner, whose 200-name universe is
part of a frozen paper strategy and is not touched). This writes ``data/depth_archive.duckdb`` with the same schema,
so ``quote_archive.bars`` / ``breadth`` read it unchanged. Over months it becomes the spread, depth and impact record
that execution calibration needs and no free source sells.

It needs the owner's Kite session of the day and runs 09:15-15:30 IST; it never places an order (the provider has no
order endpoint). One full-quote call takes up to 500 instruments; a minute's poll is 2-3 calls, well inside Kite's
quote limit, and it backs off for the rest of the minute when a call fails.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import quote_archive as QA

PATH = Path("data") / "depth_archive.duckdb"
IST = timezone(timedelta(hours=5, minutes=30))
POLL = 60
STRIKES_EACH_SIDE = 10
INDEX_OPTIONS = ("NIFTY", "BANKNIFTY")


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


def run(*, until: str = "15:31", poll: int = POLL, now_fn=None, sleep=time.sleep) -> dict:
    from ..providers import kite_data as kd
    now_fn = now_fn or (lambda: datetime.now(IST))
    spot = {}
    try:
        q = kd.quote(["NSE:NIFTY 50", "NSE:NIFTY BANK"], kind="ltp")
        spot = {"NIFTY": q.get("NSE:NIFTY 50", {}).get("last_price"), "BANKNIFTY": q.get("NSE:NIFTY BANK", {}).get("last_price")}
    except Exception:                                   # noqa: BLE001 - strikes then centre on the middle of the chain
        pass
    syms = instruments_for_today(kd, spot)
    con = QA.connect(PATH)
    stop = datetime.strptime(until, "%H:%M").time()
    polls = rows = fails = 0
    try:
        while now_fn().time() < stop:
            t0 = now_fn()
            q: dict = {}
            try:
                for i in range(0, len(syms), 450):
                    q.update(kd.quote(syms[i:i + 450], kind="full"))
                rows += QA.store(con, t0.replace(tzinfo=None), q)
                polls += 1
            except Exception:                           # noqa: BLE001 - a failed minute is skipped, not retried
                fails += 1
            sleep(max(1.0, poll - (now_fn() - t0).total_seconds()))
    finally:
        con.close()
    return {"instruments": len(syms), "polls": polls, "rows": rows, "failed_polls": fails}
