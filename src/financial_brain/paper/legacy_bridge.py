"""Shadow bridge from the legacy paper runners into the five-desk ledger (migration step 1).

The legacy runners keep running unchanged (live-v1 rules are frozen). This bridge reads their
ledgers *read-only* after the close and replays each trade into the five-desk ``PositionLedger``
and the control-room desk pages, so both views can be compared before any runner is switched.

* ``paper_live.duckdb`` table ``trades`` (live-v1 ORB/GAP intraday, long and short) -> INTRADAY desk
* ``paper_book.duckdb`` table ``positions`` (multi-day books) -> SWING desk

Every record is keyed by the legacy id, so a re-import posts nothing twice. Opening the ledgers is
refused between 09:05 and 15:30 IST. Nothing here writes to a legacy ledger or reaches a broker.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta, timezone

from financial_brain.portfolio.position_ledger import ORDER, Ev, PositionLedger, Record

IST = timezone(timedelta(hours=5, minutes=30))
MARKET = (time(9, 5), time(15, 30))
ACCOUNT = "AUTONOMOUS"
EXIT_KIND = {"stop": "STOP_HIT", "target": "TP_HIT", "time": "SESSION_FLATTEN"}


class BridgeError(RuntimeError):
    pass


def guard(now: datetime) -> None:
    t = now.astimezone(IST)
    if t.weekday() < 5 and MARKET[0] <= t.time() <= MARKET[1]:
        raise BridgeError("legacy ledgers are not opened during market hours (09:05-15:30 IST)")


def _ts(x: object, fallback: date | None = None) -> datetime:
    if isinstance(x, datetime):
        return x if x.tzinfo else x.replace(tzinfo=IST)
    if isinstance(x, date):
        return datetime(x.year, x.month, x.day, 15, 30, tzinfo=IST)
    if isinstance(x, str) and x:
        d = datetime.fromisoformat(x)
        return d if d.tzinfo else d.replace(tzinfo=IST)
    if fallback is not None:
        return datetime(fallback.year, fallback.month, fallback.day, 15, 30, tzinfo=IST)
    raise BridgeError(f"no timestamp in {x!r}")


def _snap(**kv: object) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((k, str(v)) for k, v in kv.items() if v is not None))


def intraday_records(rows: Iterable[dict]) -> list[Record]:
    out: list[Record] = []
    for r in rows:
        pid = f"INTRADAY:{r['trade_id']}"
        side = "BUY" if r["side"] == "long" else "SELL_SHORT"
        at = _ts(r["entry_ts"])
        out += [Record(f"{pid}:signal", pid, ACCOUNT, r["symbol"], Ev.ENTRY_SIGNAL, at,
                       _snap(strategy=r["strategy"], version=r["version"], mode=r["mode"], side=side)),
                Record(f"{pid}:fill", pid, ACCOUNT, r["symbol"], Ev.ENTRY_FILL, at,
                       _snap(qty=r["qty"], px=r["entry_px"])),
                Record(f"{pid}:armed", pid, ACCOUNT, r["symbol"], Ev.STOP_TP_ARMED, at,
                       _snap(sl=r["stop"], tp=r["target"],
                             close_action="SELL_TO_CLOSE" if side == "BUY" else "BUY_TO_COVER"))]
        if r.get("exit_ts") and r.get("exit_px") is not None:
            ex = _ts(r["exit_ts"])
            out += [Record(f"{pid}:trigger", pid, ACCOUNT, r["symbol"], Ev.EXIT_TRIGGER, ex,
                           _snap(reason=EXIT_KIND.get(r.get("exit_reason") or "", "UNKNOWN"))),
                    Record(f"{pid}:exit", pid, ACCOUNT, r["symbol"], Ev.EXIT_FILL, ex,
                           _snap(px=r["exit_px"], gross_pnl=r.get("gross_pnl"))),
                    Record(f"{pid}:closed", pid, ACCOUNT, r["symbol"], Ev.POSITION_CLOSED, ex, ())]
    return out


def swing_records(rows: Iterable[dict]) -> list[Record]:
    out: list[Record] = []
    for r in rows:
        pid = f"SWING:{r['version']}:{r['mode']}:{r['signal_day']}:{r['ticker']}"
        sig = _ts(r["signal_day"])
        out.append(Record(f"{pid}:signal", pid, ACCOUNT, r["ticker"], Ev.ENTRY_SIGNAL, sig,
                          _snap(book=r["version"], lineage=r.get("lineage"), horizon=r.get("horizon"),
                                rank=r.get("rank"), side="BUY")))
        if r.get("entry_day") is None or r.get("entry_px") is None:
            continue
        en = _ts(r["entry_day"])
        out += [Record(f"{pid}:fill", pid, ACCOUNT, r["ticker"], Ev.ENTRY_FILL, en,
                       _snap(qty=r.get("shares"), px=r["entry_px"])),
                Record(f"{pid}:armed", pid, ACCOUNT, r["ticker"], Ev.STOP_TP_ARMED, en,
                       _snap(sl=r.get("stop"), tp="HORIZON", close_action="SELL_TO_CLOSE"))]
        if r.get("exit_day") is not None and r.get("exit_px") is not None:
            ex = _ts(r["exit_day"])
            out += [Record(f"{pid}:trigger", pid, ACCOUNT, r["ticker"], Ev.EXIT_TRIGGER, ex,
                           _snap(reason=(r.get("exit_reason") or "UNKNOWN").upper())),
                    Record(f"{pid}:exit", pid, ACCOUNT, r["ticker"], Ev.EXIT_FILL, ex, _snap(px=r["exit_px"])),
                    Record(f"{pid}:closed", pid, ACCOUNT, r["ticker"], Ev.POSITION_CLOSED, ex, ())]
    return out


def replay(records: Iterable[Record], ledger: PositionLedger | None = None) -> tuple[PositionLedger, int]:
    """Append each position's events in lifecycle order; returns the ledger and how many records were new."""
    lg = ledger or PositionLedger()
    n = sum(lg.append(r) for r in sorted(records, key=lambda r: (r.position_id, ORDER.index(r.kind) if r.kind in ORDER else 99, r.at)))
    return lg, n


def desk_page(records: Iterable[Record], desk: str, clock: str) -> dict:
    by: dict[str, dict[str, Record]] = {}
    for r in records:
        if r.position_id.startswith(desk + ":"):
            by.setdefault(r.position_id, {})[r.kind.value] = r
    sigs = []
    for pid, ev in sorted(by.items()):
        armed = dict(ev["STOP_TP_ARMED"].snapshot) if "STOP_TP_ARMED" in ev else {}
        side = dict(ev["ENTRY_SIGNAL"].snapshot).get("side", "")
        status = "CLOSED" if "POSITION_CLOSED" in ev else "OPEN" if "ENTRY_FILL" in ev else "SIGNALLED"
        trig = dict(ev["EXIT_TRIGGER"].snapshot).get("reason", "ARMED" if armed else "-") \
            if "EXIT_TRIGGER" in ev else ("ARMED" if armed else "-")
        sigs.append({"id": pid, "side": side, "close_action": armed.get("close_action", ""),
                     "sl": armed.get("sl", ""), "tp": armed.get("tp", ""), "trigger": trig,
                     "costs": "legacy model", "status": status})
    return {"clock": clock, "signals": sigs}


def load(live_path: str, book_path: str, now: datetime) -> tuple[list[dict], list[dict]]:  # pragma: no cover
    guard(now)
    import duckdb

    def rows(path: str, sql: str) -> list[dict]:
        c = duckdb.connect(path, read_only=True)
        try:
            cur = c.execute(sql)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        finally:
            c.close()

    return (rows(live_path, "select * from trades where mode = 'live'"),
            rows(book_path, "select * from positions where mode = 'live'"))
