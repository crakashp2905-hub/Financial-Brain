"""Structured NSE disclosure listings, by month, into one generic table.

Endpoints read on 2026-10-18 from NSE's own corporate-filings pages (``corporate-filings.js``); robots.txt allows /api.

    corp_actions   /api/corporates-corporateActions      dividends, splits, bonuses, rights - ex and record dates
                                                         (from 2015): a second source beside the BSE feed
    sast_reg29     /api/corporate-sast-reg29             substantial acquisitions and disposals (SAST Reg. 29):
                                                         acquirer, mode, shares before/after (from 2016) - the
                                                         structured large-holder history the insider feed lacks
    pledge         /api/corporate-pledgedata-sast3132    promoter pledges, invocations and releases (Reg. 31/32):
                                                         lender, shares and % encumbered (from 2019)
    encumbrance    /api/corp-encumbrance                 promoters' encumbrance declarations (> 20% / > 50%)
    event_calendar /api/event-calendar                   board meetings ahead, with purpose (forward only)

Rows are kept as they came (``payload`` JSON) with a content hash as key, the dataset's event date and the
broadcast time where the listing gives one - a disclosure is usable from its broadcast, not its event date.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from ..lake.store import RawLake
from ..providers.base import FetchError

BASE = "https://www.nseindia.com"
DATASETS = {
    "corp_actions": ("/api/corporates-corporateActions?index=equities", date(2015, 1, 1), ("exDate",)),
    "sast_reg29": ("/api/corporate-sast-reg29?index=equities", date(2016, 1, 1), ("acquirerDate",)),
    "pledge": ("/api/corporate-pledgedata-sast3132?index=equities", date(2019, 1, 1), ("eventDetailsFromDate",)),
    "encumbrance": ("/api/corp-encumbrance?index=equities", date(2019, 1, 1), ("broadcastDate",)),
    "event_calendar": ("/api/event-calendar?index=equities", None, ("date",)),
}
BROADCAST = ("broadcastdate", "broadcastDate", "caBroadcastDate", "timestamp")
FORMATS = ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%d-%m-%Y")


def _ts(s) -> datetime | None:
    s = str(s or "").strip().split(" - ")[0].split(" to ")[0]
    for f in FORMATS:
        try:
            return datetime.strptime(s.title() if f.startswith("%d-%b") else s, f)
        except ValueError:
            continue
    return None


def _months(a: date, b: date):
    d = date(a.year, a.month, 1)
    while d <= b:
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        yield max(d, a), min(nxt - timedelta(days=1), b)
        d = nxt


def rows_of(payload: bytes) -> list[dict]:
    j = json.loads(payload)
    return (j.get("data") if isinstance(j, dict) else j) or []


def normalise(dataset: str, r: dict) -> dict:
    ev = next((_ts(r.get(k)) for k in DATASETS[dataset][2] if r.get(k)), None)
    bc = next((_ts(r.get(k)) for k in BROADCAST if r.get(k)), None)
    body = json.dumps(r, sort_keys=True, ensure_ascii=False)
    return {"key": hashlib.sha1(body.encode()).hexdigest()[:20], "event_date": ev.date() if ev else None,
            "broadcast_at": bc, "symbol": r.get("symbol"), "isin": r.get("isin"),
            "company": r.get("comp") or r.get("company") or r.get("companyName"), "payload": body}


def prefetch(cfg, datasets, start: date, end: date, *, provider=None) -> dict:
    from ..providers.nse_results import NSEResults
    lake, provider = RawLake(cfg.lake), provider or NSEResults(min_interval=1.0)
    out = {k: {"windows": 0, "rows": 0, "failed": 0} for k in datasets}
    for k in datasets:
        path, first, _ = DATASETS[k]
        windows = [(date.today(), date.today() + timedelta(days=45))] if first is None else _months(max(start, first), end)
        for a, b in windows:
            fn = f"{k}_{a:%Y%m%d}_{b:%Y%m%d}.json"
            if first is not None and b < date.today() - timedelta(days=7) and lake.exists("NSE", f"disc_{k}", a, fn):
                continue
            q = f"{BASE}{path}&from_date={a:%d-%m-%Y}&to_date={b:%d-%m-%Y}"
            try:
                body, _, _ = provider._get(q)
                n = len(rows_of(body))
            except (FetchError, ValueError):
                out[k]["failed"] += 1
                continue
            lake.put(source="NSE", dataset=f"disc_{k}", business_date=a, filename=fn, payload=body, url=q,
                     content_type="application/json")
            out[k]["windows"] += 1
            out[k]["rows"] += n
    return out


def load(con, cfg, datasets=tuple(DATASETS), since: date | None = None) -> dict:
    """Lake -> ``nse_disclosures`` in one bulk insert per dataset; ``since`` limits the files read (nightly)."""
    import pandas as pd
    lake = RawLake(cfg.lake)
    now = datetime.now(timezone.utc)
    out = {}
    for k in datasets:
        vals = []
        for obj in lake.iter_objects("NSE", f"disc_{k}"):
            if since and date.fromisoformat(obj.business_date) < date(since.year, since.month, 1):
                continue
            try:
                rows = rows_of(lake.read(obj.key))
            except ValueError:
                continue
            for r in rows:
                x = normalise(k, r)
                vals.append((k, x["key"], x["event_date"], x["broadcast_at"], x["symbol"], x["isin"], x["company"],
                             x["payload"], obj.key, now))
        if vals:
            df = pd.DataFrame(vals, columns=["dataset", "record_key", "event_date", "broadcast_at", "symbol", "isin",
                                             "company", "payload", "lake_key", "observed_at"]).drop_duplicates(["dataset", "record_key"])
            con.register("df", df)
            con.execute("INSERT OR IGNORE INTO nse_disclosures SELECT * FROM df")
            con.unregister("df")
        out[k] = len(vals)
    return out
