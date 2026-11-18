"""Nightly five-desk shadow run (after the close; refuses 09:05-15:30 IST).

1. Reads the legacy ledgers read-only and replays them into the five-desk ledger.
2. Flags live trades with no exit. For each one it reconstructs, from recorded minute bars,
   what the frozen rules would have done, labelled RECONSTRUCTED and never written back.
3. Writes data/paper_reports/five-desk-<day>.html (control room) and .json (summary).

Exit code 2 when unexited trades exist, so a scheduler or alert can flag it the same day.

Usage: python scripts/five_desk_nightly.py [--root D:/Finance-Brain] [--kite]
  --kite  fetch minute bars from Kite history when the local store lacks the session
"""
from __future__ import annotations

import functools
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from financial_brain.paper import legacy_bridge as lb
from financial_brain.paper.dashboard_html import render


def _rows(path: Path, sql: str, params: list | None = None) -> list[dict]:
    import duckdb
    c = duckdb.connect(str(path), read_only=True)
    try:
        cur = c.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    finally:
        c.close()


@functools.cache
def _nse() -> list[dict]:
    from financial_brain.providers import kite_data as kd
    return kd.instruments("NSE")


def _kite_bars(symbol: str, day: str, root: Path) -> list[tuple]:
    """Minute bars for one past session from Kite history (read-only, owner's daily session)."""
    from datetime import date

    from financial_brain import env
    from financial_brain.providers import kite_data as kd
    env.load(root / ".env")
    os.environ.setdefault("FB_DATA_ROOT", str(root / "data"))
    tok = kd.token_for(symbol, _nse())
    if tok is None:
        return []
    d = date.fromisoformat(day)
    rows = kd.candles(tok, datetime(d.year, d.month, d.day, 9, 15), datetime(d.year, d.month, d.day, 15, 30),
                      interval="minute")
    return [(datetime.fromisoformat(r["ts"]) if isinstance(r["ts"], str) else r["ts"],
             r["open"], r["high"], r["low"], r["close"]) for r in rows]


def main(root: Path, use_kite: bool = False) -> int:
    now = datetime.now(lb.IST)
    lb.guard(now)
    data = root / "data"
    trades = _rows(data / "paper_live.duckdb", "select * from trades where mode = 'live'")
    runs = _rows(data / "paper_live.duckdb", "select day, status, note from runs where mode = 'live'")
    book = _rows(data / "paper_book.duckdb", "select * from positions where mode = 'live'")
    recs = lb.intraday_records(trades) + lb.swing_records(book)
    _, posted = lb.replay(recs)

    missing = []
    for t in lb.unexited(trades, runs):
        bars = _rows(data / "minute_history.duckdb",
                     "select ts, open, high, low, close from minute_bars where tradingsymbol = ? "
                     "and cast(ts at time zone 'Asia/Kolkata' as date) = cast(? as date) order by ts",
                     [t["symbol"], str(t["day"])])
        series = [(b["ts"], b["open"], b["high"], b["low"], b["close"]) for b in bars]
        if not series and use_kite:
            series = _kite_bars(t["symbol"], str(t["day"]), root)
        r = lb.reconstruct_exit(t, series)
        missing.append({"trade_id": t["trade_id"], "day": str(t["day"]), "run_status": t["run_status"],
                        "reason": r["reason"], "px": r["px"], "basis": r["basis"],
                        "gross_estimate": lb.gross(t, r["px"])})

    closed = [t for t in trades if t.get("exit_px") is not None]
    realized = sum(float(t.get("net_pnl") or t.get("gross_pnl") or 0) for t in closed)
    today = [{"level": 1, "kind": "EXIT_UNPRICED", "desk": "INTRADAY", "at": m["day"],
              "text": f"{m['trade_id']}: no exit in ledger (run {m['run_status']}); "
                      f"reconstructed {m['reason']} @ {m['px']}", "rule_version": "live-v1",
              "evidence_tier": "RECONSTRUCTED"} for m in missing]
    state = {
        "consolidated": {"nav": "UNPRICED", "available": "-", "blocked": "-", "realized": round(realized, 2),
                         "unrealized": "STALE", "drawdown": "-"},
        "today": today,
        "desks": {"INTRADAY": lb.desk_page(recs, "INTRADAY", "live 1-min polling (legacy live-v1)"),
                  "SWING": lb.desk_page(recs, "SWING", "daily close (legacy paper book)")},
        "health": {"status": "DEGRADED" if missing else "OK",
                   "problems": [f"{len(missing)} live trades without exit"] if missing else [],
                   "pending_mf": 0, "expiring_options": 0},
        "heatmap": [], "option_cards": [],
    }
    out = data / "paper_reports"
    out.mkdir(exist_ok=True)
    day = now.date().isoformat()
    (out / f"five-desk-{day}.html").write_text(render(state), encoding="utf-8")
    summary = {"at": now.isoformat(), "records": len(recs), "new_records": posted,
               "intraday_trades": len(trades), "swing_positions": len(book),
               "unexited": len(missing), "unexited_by_reason": Counter(m["reason"] for m in missing),
               "unexited_gross_estimate": round(sum(m["gross_estimate"] or 0 for m in missing), 2),
               "missing": missing}
    (out / f"five-desk-{day}.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "missing"}, default=str))
    return 2 if missing else 0


if __name__ == "__main__":
    root = Path(sys.argv[sys.argv.index("--root") + 1]) if "--root" in sys.argv else Path.cwd()
    sys.exit(main(root, use_kite="--kite" in sys.argv))
