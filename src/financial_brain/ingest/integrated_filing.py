"""NSE Integrated Filing - Financials (results from the March-2025 quarter on) into the same results store as
``ingest/xbrl_results.py``.

From the March-2025 quarter NSE moved results to "Integrated Filing". The page
(https://www.nseindia.com/companies-listing/corporate-integrated-filing) lists filings through

    /api/integrated-filing-results?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY&period_ended=all
        &type=Integrated%20Filing-%20Financials&page=N&size=100

(read on 2026-10-15 from the page's own requests in a browser; robots.txt allows /api), each row linking its XBRL on
the archive host. The XBRL uses SEBI's ``in-capmkt`` taxonomy with the same concepts and contexts as the older results
XBRL, which ``docintel/xbrl_results.parse`` now accepts.

Two phases, as every backfill here: ``prefetch`` (network: listings and XBRL into the raw lake, no database) and
``load`` (lake -> ``xbrl_filings``, ``xbrl_facts``, ``financial_results``; no network). A revision is a new filing with
its own time, so a restatement is a new row and ``as_known_on`` still answers what was known on a date.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from ..docintel import xbrl_results as X
from ..lake.store import RawLake
from ..providers.base import FetchError
from ..providers.nse_results import NSEResults
from . import xbrl_results as XR

API = "https://www.nseindia.com/api/integrated-filing-results"
TYPE = "Integrated Filing- Financials"
FIRST = date(2025, 4, 1)
LISTING = "integrated_listing"
PAGE = 100


def _months(start: date, end: date):
    d = date(start.year, start.month, 1)
    while d <= end:
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        yield max(d, start), min(nxt - timedelta(days=1), end)
        d = nxt


def listing(provider: NSEResults, a: date, b: date) -> list[dict]:
    rows, page = [], 1
    while True:
        q = (f"{API}?index=equities&from_date={a:%d-%m-%Y}&to_date={b:%d-%m-%Y}&period_ended=all"
             f"&type=Integrated%20Filing-%20Financials&page={page}&size={PAGE}")
        body, _, _ = provider._get(q)
        j = json.loads(body)
        data = j.get("data") or []
        rows += data
        if not data or len(rows) >= int(j.get("totalCount") or 0):
            return rows
        page += 1


def prefetch(cfg, start: date, end: date, *, symbols: set[str] | None = None, provider=None, on_progress=None) -> dict:
    """Listings and XBRL into the lake. ``symbols`` limits the XBRL downloads (listings are kept whole)."""
    lake, provider = RawLake(cfg.lake), provider or NSEResults()
    out = {"months": 0, "listed": 0, "xbrl_stored": 0, "xbrl_failed": 0, "skipped_symbol": 0}
    for a, b in _months(max(start, FIRST), end):
        rows = listing(provider, a, b)
        lake.put(source="NSE", dataset=LISTING, business_date=a, filename=f"listing_{a:%Y%m%d}_{b:%Y%m%d}.json",
                 payload=json.dumps(rows).encode(), url=f"{API}?from_date={a:%d-%m-%Y}&to_date={b:%d-%m-%Y}")
        out["months"] += 1
        out["listed"] += len(rows)
        for r in rows:
            if symbols is not None and r.get("symbol") not in symbols:
                out["skipped_symbol"] += 1
                continue
            url = r.get("xbrl") or ""
            if not url.lower().endswith(".xml"):
                continue
            fn = url.rsplit("/", 1)[-1]
            when = XR._ts(r.get("broadcast_Date")) or XR._ts(r.get("creation_Date")) or datetime.combine(a, datetime.min.time())
            if lake.exists("NSE", "xbrl_results", when.date(), fn):
                continue
            try:
                res = provider.fetch_xbrl(url)
            except FetchError:
                out["xbrl_failed"] += 1
                continue
            lake.put(source="NSE", dataset="xbrl_results", business_date=when.date(), filename=res.filename,
                     payload=res.payload, url=res.url, content_type=res.content_type, http_status=res.http_status,
                     retrieved_at=res.retrieved_at)
            out["xbrl_stored"] += 1
        if on_progress:
            on_progress(a, out)
    return out


def _row(r: dict, isin: str | None, inst: X.Instance | None) -> dict:
    """An integrated-filing row in the shape the results store's helpers take."""
    start = inst.period[0] if inst and inst.period else None
    return {"seqNumber": f"IF{r['seq_Id']}", "symbol": r.get("symbol"), "isin": isin, "companyName": r.get("cmName"),
            "fromDate": datetime.fromisoformat(start).strftime("%d-%b-%Y") if start else None,
            "toDate": r.get("qe_Date"), "consolidated": r.get("consolidated"), "relatingTo": r.get("type_Sub"),
            "audited": r.get("audited"), "cumulative": "Non-cumulative", "bank": "N",
            "exchdisstime": r.get("broadcast_Date") or r.get("revised_Date"), "broadCastDate": r.get("creation_Date"),
            "xbrl": r.get("xbrl")}


def load(con, cfg, start: date, end: date) -> dict:
    """Lake -> results store. Idempotent: filings already stored are skipped."""
    lake = RawLake(cfg.lake)
    isin_of = dict(con.execute("""SELECT ticker, ANY_VALUE(isin) FROM adjusted_prices
                                  WHERE business_date >= '2025-01-01' AND isin IS NOT NULL GROUP BY ticker""").fetchall())
    done = {r[0] for r in con.execute("SELECT seq_number FROM xbrl_filings WHERE seq_number LIKE 'IF%'").fetchall()}
    out = {"rows": 0, "stored": 0, "promoted": 0, "no_isin": 0, "no_file": 0, "parse_failed": 0, "mismatch": 0,
           "skipped": 0}
    for a, b in _months(max(start, FIRST), end):
        key = f"NSE/{LISTING}/{a:%Y}/{a:%m}/{a:%d}/listing_{a:%Y%m%d}_{b:%Y%m%d}.json"
        if lake.meta(key) is None:
            continue
        for r in json.loads(lake.read(key)):
            out["rows"] += 1
            if f"IF{r['seq_Id']}" in done:
                out["skipped"] += 1
                continue
            url = r.get("xbrl") or ""
            fn = url.rsplit("/", 1)[-1]
            when = XR._ts(r.get("broadcast_Date")) or XR._ts(r.get("creation_Date"))
            obj_key = f"NSE/xbrl_results/{when:%Y/%m/%d}/{fn}" if when else None
            if not obj_key or lake.meta(obj_key) is None:
                out["no_file"] += 1
                continue
            XR._register(con, lake.meta(obj_key))
            isin = isin_of.get(r.get("symbol"))
            try:
                inst = X.parse(lake.read(obj_key))
            except X.XBRLError:
                XR._record(con, _row(r, isin, None), XR.PARSE_FAILED, obj_key)
                out["parse_failed"] += 1
                continue
            row = _row(r, isin, inst)
            got_end = date.fromisoformat(inst.period[1]) if inst.period and inst.period[1] else None
            if got_end and XR._d(row["toDate"]) and got_end != XR._d(row["toDate"]):
                XR._record(con, row, XR.MISMATCH, obj_key)
                out["mismatch"] += 1
                continue
            XR._store_facts(con, row["seqNumber"], inst)
            if isin is None:
                out["no_isin"] += 1
            elif XR._promote(con, row, inst, obj_key):
                out["promoted"] += 1
            XR._record(con, row, XR.STORED, obj_key)
            out["stored"] += 1
    return out
