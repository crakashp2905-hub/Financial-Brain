"""NSE bulk deals, block deals and short-selling disclosures, by month.

``/api/historicalOR/bulk-block-short-deals?optionType={bulk_deals|block_deals|short_selling}&from=&to=&csv=true``
(read on 2026-10-18 from NSE's own "Bulk Deals / Block Deals / Short Selling Archives" page; the JSON form is capped at
70 rows, the CSV form is complete). Bulk deals go back to at least 2015.

    bulk_deals     a client trading more than 0.5% of a company's shares in a day, with the client's name
    block_deals    trades in the block window (minimum size, separate session)
    short_selling  shares sold short by institutions, disclosed per day

Stored in ``bulk_block_deals`` and ``short_selling``. A month's file replaces that month's rows (idempotent). Event
time is the trade date; the disclosure is published that evening, so a signal may use it from the next session.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone

from ..lake.store import RawLake
from ..providers.base import FetchError

API = "https://www.nseindia.com/api/historicalOR/bulk-block-short-deals"
KINDS = ("bulk_deals", "block_deals", "short_selling")


def _months(a: date, b: date):
    d = date(a.year, a.month, 1)
    while d <= b:
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        yield max(d, a), min(nxt - timedelta(days=1), b)
        d = nxt


def _num(s: str):
    s = (s or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse(text: str) -> list[dict]:
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        return []
    head = [h.strip().lower() for h in rows[0]]
    return [dict(zip(head, (c.strip() for c in r))) for r in rows[1:] if any(c.strip() for c in r)]


def prefetch(cfg, start: date, end: date, *, kinds=KINDS, provider=None) -> dict:
    from ..providers.nse_results import NSEResults
    lake, provider = RawLake(cfg.lake), provider or NSEResults(min_interval=1.0)
    out = {k: {"months": 0, "failed": 0} for k in kinds}
    for a, b in _months(start, end):
        for k in kinds:
            fn = f"{k}_{a:%Y%m%d}_{b:%Y%m%d}.csv"
            if b < date.today() - timedelta(days=7) and lake.exists("NSE", k, a, fn):
                continue                                # a closed month already held
            q = f"{API}?optionType={k}&from={a:%d-%m-%Y}&to={b:%d-%m-%Y}&csv=true"
            try:
                body, _, ctype = provider._get(q)
            except FetchError:
                out[k]["failed"] += 1
                continue
            lake.put(source="NSE", dataset=k, business_date=a, filename=fn, payload=body, url=q,
                     content_type=ctype or "text/csv")
            out[k]["months"] += 1
    return out


def load(con, cfg, start: date, end: date) -> dict:
    lake = RawLake(cfg.lake)
    now = datetime.now(timezone.utc)
    out = {"deals": 0, "short": 0, "months": 0}
    for a, b in _months(start, end):
        for k in KINDS:
            key = f"NSE/{k}/{a:%Y}/{a:%m}/{a:%d}/{k}_{a:%Y%m%d}_{b:%Y%m%d}.csv"
            if lake.meta(key) is None:
                continue
            rows = parse(lake.read(key).decode("utf-8", "replace"))
            if k == "short_selling":
                con.execute("DELETE FROM short_selling WHERE trade_date BETWEEN ? AND ?", [a, b])
                vals = []
                for r in rows:
                    d = _date(r.get("date"))
                    if d:
                        vals.append([d, r.get("symbol"), r.get("security name"), _num(r.get("quantity")), now])
                _bulk(con, "short_selling", vals, ["trade_date", "symbol", "security", "qty", "observed_at"])
                out["short"] += len(vals)
            else:
                kind = "bulk" if k == "bulk_deals" else "block"
                con.execute("DELETE FROM bulk_block_deals WHERE kind = ? AND trade_date BETWEEN ? AND ?", [kind, a, b])
                vals = []
                for i, r in enumerate(rows):
                    d = _date(r.get("date"))
                    if d:
                        vals.append([kind, d, i, r.get("symbol"), r.get("security name"), r.get("client name"),
                                     (r.get("buy / sell") or "").upper(), _num(r.get("quantity traded")),
                                     _num(r.get("trade price / wght. avg. price")), r.get("remarks"), now])
                _bulk(con, "bulk_block_deals", vals, ["kind", "trade_date", "seq", "symbol", "security", "client", "side",
                                                      "qty", "price", "remarks", "observed_at"])
                out["deals"] += len(vals)
            out["months"] += 1
    return out


def _bulk(con, table: str, vals: list, cols: list[str]) -> None:
    if not vals:
        return
    import pandas as pd
    con.register("df", pd.DataFrame(vals, columns=cols))
    con.execute(f"INSERT INTO {table} SELECT * FROM df")
    con.unregister("df")


def _date(s):
    try:
        return datetime.strptime((s or "").strip(), "%d-%b-%Y").date()
    except ValueError:
        return None
