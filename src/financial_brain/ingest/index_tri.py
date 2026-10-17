"""NSE index total-return series and constituent snapshots, from niftyindices.com (NSE Indices Ltd).

Total return: ``POST /BackPage/getTotalReturnIndexString`` with ``{"cinfo": "{'name':..,'startDate':'dd-Mon-yyyy',
'endDate':..,'indexName':..}"}`` (read from the site's own historical-data page on 2026-10-18; robots.txt allows it).
Each row gives the gross total-return index and the net total-return index (after dividend tax). Benchmarks become
total return rather than price - a price index understates a long book's benchmark by the dividend yield.

Constituents: only the **current** list is published (``/IndexConstituent/ind_<name>list.csv``), so history cannot be
backfilled for free. Each night's list is kept, which builds a point-in-time membership record from 2026-10-18 on.
"""
from __future__ import annotations

import csv
import io
import json
import urllib.request
from datetime import date, datetime, timedelta, timezone

from ..lake.store import RawLake

BASE = "https://www.niftyindices.com"
TRI_INDICES = ("NIFTY 50", "NIFTY 500", "NIFTY NEXT 50", "NIFTY MIDCAP 150", "NIFTY SMALLCAP 250", "NIFTY BANK",
               "NIFTY AUTO", "NIFTY ENERGY", "NIFTY FMCG", "NIFTY IT", "NIFTY MEDIA", "NIFTY METAL", "NIFTY PHARMA",
               "NIFTY PSU BANK", "NIFTY REALTY", "NIFTY INFRASTRUCTURE", "NIFTY COMMODITIES",
               "NIFTY INDIA CONSUMPTION", "NIFTY ALPHA QUALITY VALUE LOW-VOLATILITY 30")
LISTS = {"NIFTY 50": "nifty50", "NIFTY NEXT 50": "niftynext50", "NIFTY 100": "nifty100", "NIFTY 200": "nifty200",
         "NIFTY 500": "nifty500", "NIFTY MIDCAP 150": "niftymidcap150", "NIFTY SMALLCAP 250": "niftysmallcap250"}
UA = "Mozilla/5.0 (financial-brain personal research)"


def _post(path: str, payload: dict) -> bytes:
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(), method="POST",
                                 headers={"User-Agent": UA, "Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def _get(path: str) -> bytes:
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def fetch_tri(name: str, a: date, b: date) -> bytes:
    cinfo = f"{{'name':'{name}','startDate':'{a:%d-%b-%Y}','endDate':'{b:%d-%b-%Y}','indexName':'{name}'}}"
    return _post("/BackPage/getTotalReturnIndexString", {"cinfo": cinfo})


def parse_tri(payload: bytes) -> list[tuple[date, float, float | None]]:
    j = json.loads(payload)
    rows = json.loads(j["d"]) if isinstance(j, dict) and "d" in j else j
    out = []
    for r in rows or []:
        try:
            d = datetime.strptime(r["Date"].strip(), "%d %b %Y").date()
            tri = float(str(r["TotalReturnsIndex"]).replace(",", ""))
        except (KeyError, ValueError):
            continue
        ntr = r.get("NTR_Value")
        try:
            ntr = float(str(ntr).replace(",", "")) if ntr not in (None, "", "-") else None
        except ValueError:
            ntr = None
        out.append((d, tri, ntr))
    return out


def prefetch(cfg, start: date, end: date, *, tri_fn=fetch_tri, list_fn=_get) -> dict:
    """Total return in yearly requests (keeps each response small), and today's constituent lists."""
    lake = RawLake(cfg.lake)
    out = {"tri": {}, "lists": 0, "failed": []}
    for name in TRI_INDICES:
        n = 0
        y = start.year
        while y <= end.year:
            a, b = max(start, date(y, 1, 1)), min(end, date(y, 12, 31))
            fn = f"{name.replace(' ', '_')}_{a:%Y%m%d}_{b:%Y%m%d}.json"
            if not (b < date.today() - timedelta(days=7) and lake.exists("NIFTYINDICES", "tri", a, fn)):
                try:
                    body = tri_fn(name, a, b)
                    lake.put(source="NIFTYINDICES", dataset="tri", business_date=a, filename=fn, payload=body,
                             url=BASE + "/BackPage/getTotalReturnIndexString", content_type="application/json")
                    n += 1
                except OSError as e:
                    out["failed"].append(f"{name} {y}: {e}")
            y += 1
        out["tri"][name] = n
    today = date.today()
    for name, slug in LISTS.items():
        try:
            body = list_fn(f"/IndexConstituent/ind_{slug}list.csv")
        except OSError as e:
            out["failed"].append(f"list {name}: {e}")
            continue
        lake.put(source="NIFTYINDICES", dataset="constituents", business_date=today, filename=f"ind_{slug}list.csv",
                 payload=body, url=f"{BASE}/IndexConstituent/ind_{slug}list.csv", content_type="text/csv")
        out["lists"] += 1
    return out


def load(con, cfg, since: date | None = None) -> dict:
    """Lake -> tables. ``since`` limits the work to files from that date (the nightly run passes a month back)."""
    import pandas as pd
    lake = RawLake(cfg.lake)
    now = datetime.now(timezone.utc)
    out = {"tri_rows": 0, "members": 0}
    frames = []
    for obj in lake.iter_objects("NIFTYINDICES", "tri"):
        if since and date.fromisoformat(obj.business_date) < date(since.year, 1, 1):
            continue
        name = obj.filename.rsplit("_", 2)[0].replace("_", " ")
        frames += [(name, d, tri, ntr, now) for d, tri, ntr in parse_tri(lake.read(obj.key))]
    if frames:
        df = pd.DataFrame(frames, columns=["index_name", "business_date", "tri", "ntr", "observed_at"])
        df = df.drop_duplicates(["index_name", "business_date"], keep="last")
        con.register("df", df)
        con.execute("INSERT OR REPLACE INTO index_total_return SELECT * FROM df")
        con.unregister("df")
        out["tri_rows"] = len(df)
    for obj in lake.iter_objects("NIFTYINDICES", "constituents"):
        if since and date.fromisoformat(obj.business_date) < since:
            continue
        d = date.fromisoformat(obj.business_date)
        slug = obj.filename[len("ind_"):-len("list.csv")]
        name = next((k for k, v in LISTS.items() if v == slug), slug)
        rows = list(csv.DictReader(io.StringIO(lake.read(obj.key).decode("utf-8-sig", "replace"))))
        con.execute("DELETE FROM index_membership WHERE index_name = ? AND as_of = ?", [name, d])
        con.executemany("INSERT INTO index_membership VALUES (?,?,?,?,?,?)",
                        [[name, d, r.get("Symbol"), r.get("ISIN Code"), r.get("Industry"), now] for r in rows])
        out["members"] += len(rows)
    return out
