"""Import past trades from a broker export, so the journal is not limited to what Kite's same-day trade book held.

Zerodha Console's tradebook export (CSV) has, per fill: symbol, isin, trade_date, exchange, segment, series,
trade_type, auction, quantity, price, trade_id, order_id, order_execution_time. Rows go into ``my_trades`` beside the
nightly sync; ``trade_id`` is the key, so importing the same file twice, or a file overlapping the sync, adds nothing.

Contract notes (PDF) carry the exchange's charges as billed; reconciling them is not done here - the export carries
fills, not charges, and the cost model's charges stay modelled until contract notes are parsed.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime

PRODUCT = {"EQ": "CNC", "FO": "NRML", "CDS": "NRML", "COM": "NRML"}
NEEDED = {"symbol", "trade_date", "trade_type", "quantity", "price", "trade_id"}


class ImportError_(ValueError):
    pass


def parse(text: str) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        return []
    cols = {k.strip().lower() for k in rows[0]}
    missing = NEEDED - cols
    if missing:
        raise ImportError_(f"not a tradebook export: missing {sorted(missing)}")
    out = []
    for raw in rows:
        r = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        side = r["trade_type"].lower()
        if side not in ("buy", "sell"):
            raise ImportError_(f"trade {r['trade_id']}: trade_type {r['trade_type']!r}")
        day = date.fromisoformat(r["trade_date"][:10])
        ts = r.get("order_execution_time") or f"{day}T00:00:00"
        try:
            ts = datetime.fromisoformat(ts.replace(" ", "T")).isoformat()
        except ValueError:
            ts = f"{day}T00:00:00"
        seg = (r.get("segment") or "EQ").upper()
        out.append({"trade_id": r["trade_id"], "day": day, "ts": ts, "tradingsymbol": r["symbol"],
                    "exchange": r.get("exchange") or "NSE", "product": PRODUCT.get(seg, "CNC"), "side": side,
                    "quantity": float(r["quantity"]), "price": float(r["price"])})
    return out


def load(con, rows: list[dict]) -> dict:
    before = con.execute("SELECT COUNT(*) FROM my_trades").fetchone()[0]
    con.executemany("INSERT OR IGNORE INTO my_trades VALUES (?,?,?,?,?,?,?,?,?)",
                    [[r["trade_id"], r["day"], r["ts"], r["tradingsymbol"], r["exchange"], r["product"], r["side"],
                      r["quantity"], r["price"]] for r in rows])
    after = con.execute("SELECT COUNT(*) FROM my_trades").fetchone()[0]
    return {"rows": len(rows), "added": after - before, "already_present": len(rows) - (after - before)}
