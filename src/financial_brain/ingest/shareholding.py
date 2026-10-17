"""NSE shareholding patterns (Reg. 31): who owns each listed company, quarter by quarter, point in time.

The listing ``/api/corporate-share-holdings-master?index=equities&symbol=X`` returns a company's filings (from the
September-2021 quarter: about five years), and ``...&from_date=&to_date=`` the filings of a window (nightly). Each row
carries promoter and public percentages, broadcast and submission times, a revision flag and an XBRL instance
(``in-bse-shp``) with every category's holding: promoter group, mutual funds, insurers, FPIs, banks, retail,
corporates... and promoter shares pledged or encumbered where there are any.

Point in time: a filing counts from the **later** of its broadcast and submission times (old rows were re-broadcast
after revisions; the later time is the safe one). A revision is a new row, never an overwrite.

Stored: ``shareholding_filings`` (one row per filing) and ``shareholding_facts`` (category x concept x value, only
the category totals - contexts ending ``_ContextI`` - not the per-holder rows, which carry names and PANs).
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

from ..lake.store import RawLake
from ..providers.base import FetchError

API = "https://www.nseindia.com/api/corporate-share-holdings-master?index=equities"
LISTING, XBRL = "shp_listing", "shp_xbrl"
NS_PREFIX = "{http://www.bseindia.com/xbrl/shp/"
SKIP = re.compile(r"Name|PermanentAccountNumber|Whether|Type|Category|Symbol|ScripCode|ISIN|Date|Class", re.I)


def _ts(s):
    for f in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
        try:
            return datetime.strptime((s or "").strip(), f)
        except ValueError:
            continue
    return None


def known_at(r: dict) -> datetime | None:
    ts = [t for t in (_ts(r.get("broadcastDate")), _ts(r.get("submissionDate"))) if t]
    return max(ts) if ts else None


def parse_xbrl(payload: bytes) -> list[tuple[str, str, float]]:
    """(category, concept, value) for every numeric fact in a category-total context."""
    root = ET.fromstring(payload)
    out = []
    for el in root:
        ctx = el.get("contextRef") or ""
        if not ctx.endswith("_ContextI"):
            continue
        concept = el.tag.rsplit("}", 1)[-1]
        if SKIP.search(concept):
            continue
        try:
            v = float((el.text or "").strip())
        except ValueError:
            continue
        out.append((ctx[: -len("_ContextI")], concept, v))
    return out


def prefetch(cfg, *, symbols: list[str] | None = None, start: date | None = None, end: date | None = None,
             xbrl: bool = True, provider=None, on_progress=None) -> dict:
    """Per-symbol listings (backfill) or a date window (nightly), and each filing's XBRL."""
    from ..providers.nse_results import NSEResults
    lake, provider = RawLake(cfg.lake), provider or NSEResults(min_interval=0.5)
    today = date.today()
    out = {"listings": 0, "rows": 0, "xbrl_stored": 0, "xbrl_had": 0, "failed": 0}
    queries = ([(f"{API}&symbol={s}", f"sym_{s}.json") for s in symbols] if symbols else
               [(f"{API}&from_date={start:%d-%m-%Y}&to_date={end:%d-%m-%Y}", f"window_{start:%Y%m%d}_{end:%Y%m%d}.json")])
    for k, (q, fn) in enumerate(queries):
        try:
            body, _, _ = provider._get(q)
            rows = json.loads(body)
        except (FetchError, ValueError):
            out["failed"] += 1
            continue
        lake.put(source="NSE", dataset=LISTING, business_date=today, filename=fn, payload=json.dumps(rows).encode(), url=q)
        out["listings"] += 1
        out["rows"] += len(rows)
        if xbrl:
            for r in rows:
                u = r.get("xbrl") or ""
                when = known_at(r)
                if not u.lower().endswith(".xml") or when is None:
                    continue
                x = u.rsplit("/", 1)[-1]
                if lake.exists("NSE", XBRL, when.date(), x):
                    out["xbrl_had"] += 1
                    continue
                try:
                    res = provider.fetch_xbrl(u)
                except FetchError:
                    out["failed"] += 1
                    continue
                lake.put(source="NSE", dataset=XBRL, business_date=when.date(), filename=res.filename, payload=res.payload,
                         url=res.url, content_type=res.content_type, http_status=res.http_status, retrieved_at=res.retrieved_at)
                out["xbrl_stored"] += 1
        if on_progress and k % 50 == 0:
            on_progress(k, out)
    return out


def load(con, cfg) -> dict:
    """Every listing in the lake -> ``shareholding_filings``; each filing's XBRL -> ``shareholding_facts``.
    Idempotent on the record id."""
    lake = RawLake(cfg.lake)
    done = {r[0] for r in con.execute("SELECT record_id FROM shareholding_filings WHERE facts > 0").fetchall()}
    now = datetime.now(timezone.utc)
    out = {"filings": 0, "facts": 0, "no_xbrl": 0, "parse_failed": 0, "skipped": 0}
    seen = set()
    filings: list = []
    fact_rows: list = []
    for obj in lake.iter_objects("NSE", LISTING):
        for r in json.loads(lake.read(obj.key)):
            rid = str(r.get("recordId"))
            if rid in seen:
                continue
            seen.add(rid)
            if rid in done:
                out["skipped"] += 1
                continue
            when = known_at(r)
            u = r.get("xbrl") or ""
            key = f"NSE/{XBRL}/{when:%Y/%m/%d}/{u.rsplit('/', 1)[-1]}" if when and u else None
            facts = []
            if key and lake.meta(key) is not None:
                try:
                    facts = parse_xbrl(lake.read(key))
                except ET.ParseError:
                    out["parse_failed"] += 1
            else:
                out["no_xbrl"] += 1
            period = _ts(r.get("date"))
            filings.append((rid, r.get("symbol"), r.get("isin"), period.date() if period else None, known_at(r),
                            _ts(r.get("broadcastDate")), _ts(r.get("submissionDate")), str(r.get("revisedData") or ""),
                            _f(r.get("pr_and_prgrp")), _f(r.get("public_val")), key, len(facts), now))
            fact_rows += [(rid, c, k, v) for c, k, v in facts]
            out["filings"] += 1
            out["facts"] += len(facts)
            if len(fact_rows) > 500_000:
                _flush(con, filings, fact_rows)
                filings, fact_rows = [], []
    _flush(con, filings, fact_rows)
    return out


def _flush(con, filings: list, fact_rows: list) -> None:
    import pandas as pd
    if filings:
        f = pd.DataFrame(filings, columns=["record_id", "symbol", "isin", "period_end", "known_at", "broadcast_at",
                                           "submitted_at", "revised", "promoter_pct", "public_pct", "lake_key", "facts",
                                           "observed_at"])
        con.register("f", f)
        con.execute("INSERT OR REPLACE INTO shareholding_filings SELECT * FROM f")
        con.unregister("f")
    if fact_rows:
        x = pd.DataFrame(fact_rows, columns=["record_id", "category", "concept", "value"]).drop_duplicates(
            ["record_id", "category", "concept"])
        con.register("x", x)
        con.execute("DELETE FROM shareholding_facts WHERE record_id IN (SELECT DISTINCT record_id FROM x)")
        con.execute("INSERT INTO shareholding_facts SELECT * FROM x")
        con.unregister("x")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def window(today: date | None = None) -> tuple[date, date]:
    t = today or date.today()
    return t - timedelta(days=3), t
