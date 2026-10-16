"""NSE structured insider-trading disclosures (SEBI PIT, XBRL) - a forward record from July 2026.

The page https://www.nseindia.com/companies-listing/corporate-filings-insider-trading lists XBRL disclosures through

    /api/corporates-pit-gg?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY

(read on 2026-10-15 from the page's own requests; robots.txt allows /api). Each listing row links one XBRL instance
on the archive host; each instance holds one or more disclosures (contexts ``Disclosure1``, ``Disclosure2``, ...):
the person's category, buy or sell, number and value of securities, holding before and after, mode and dates.

History starts in July 2026 (earlier months return nothing), so this is collected nightly and kept: an insider
net-buying signal can be tested out of sample only once months of it exist.

Two phases as every source here: ``prefetch`` (network -> raw lake, no database) and ``load`` (lake ->
``insider_disclosures``, no network). A revised filing is its own row with its own time.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

from ..lake.store import RawLake
from ..providers.base import FetchError
from ..providers.nse_results import NSEResults
from . import xbrl_results as XR

API = "https://www.nseindia.com/api/corporates-pit-gg"
FIRST = date(2026, 7, 1)
LISTING, DATASET = "insider_listing", "insider_xbrl"
XBRLI = "{http://www.xbrl.org/2003/instance}"

FIELDS = {
    "CategoryOfPerson": "category", "NameOfThePerson": "person", "TypeOfInstrument": "instrument",
    "SecuritiesAcquiredOrDisposedTransactionType": "side", "SecuritiesAcquiredOrDisposedNumberOfSecurity": "qty",
    "SecuritiesAcquiredOrDisposedValueOfSecurity": "value_inr",
    "SecuritiesHeldPriorToAcquisitionOrDisposalNumberOfSecurity": "held_before",
    "SecuritiesHeldPriorToAcquisitionOrDisposalPercentageOfShareholding": "pct_before",
    "SecuritiesHeldPostAcquistionOrDisposalNumberOfSecurity": "held_after",
    "SecuritiesHeldPostAcquistionOrDisposalPercentageOfShareholding": "pct_after",
    "ModeOfAcquisitionOrDisposal": "mode",
    "DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyFromDate": "trade_from",
    "DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyToDate": "trade_to",
    "DateOfIntimationToCompany": "intimated_on", "ExchangeOnWhichTheTradeWasExecuted": "exchange",
}
NUMERIC = {"qty", "value_inr", "held_before", "pct_before", "held_after", "pct_after"}
MAIN = {"Symbol": "symbol", "ISINCode": "isin", "NameOfTheCompany": "company", "DisclosureUnderRegulation": "regulation",
        "RevisedFilling": "revised", "DateOfFiling": "filed_on"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse(payload: bytes) -> list[dict]:
    """One dict per disclosure in the instance, each carrying the filing-level fields."""
    root = ET.fromstring(payload)
    main, by_ctx = {}, {}
    for el in root:
        name, ctx = _local(el.tag), el.get("contextRef")
        if ctx is None or el.tag.startswith(XBRLI):
            continue
        text = (el.text or "").strip()
        if name in MAIN:
            main[MAIN[name]] = text
        elif name in FIELDS:
            k = FIELDS[name]
            if k in NUMERIC:
                try:
                    val = float(text)
                except ValueError:
                    val = None
            else:
                val = text or None
            by_ctx.setdefault(ctx, {})[k] = val
    out = []
    for ctx in sorted(by_ctx, key=lambda c: int(re.sub(r"\D", "", c) or 0)):
        d = {**main, **by_ctx[ctx], "disclosure": ctx}
        d["revised"] = str(d.get("revised", "")).lower() == "true"
        for k in ("trade_from", "trade_to", "intimated_on", "filed_on"):
            try:
                d[k] = date.fromisoformat(d[k]) if d.get(k) else None
            except ValueError:
                d[k] = None                                # "NA" and the like
        out.append(d)
    return out


def _days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def listing(provider: NSEResults, day: date) -> list[dict]:
    body, _, _ = provider._get(f"{API}?index=equities&from_date={day:%d-%m-%Y}&to_date={day:%d-%m-%Y}")
    j = json.loads(body)
    return (j.get("data") if isinstance(j, dict) else j) or []


def prefetch(cfg, start: date, end: date, *, provider=None) -> dict:
    lake, provider = RawLake(cfg.lake), provider or NSEResults()
    out = {"days": 0, "listed": 0, "xbrl_stored": 0, "xbrl_failed": 0}
    for day in _days(max(start, FIRST), end):
        rows = listing(provider, day)
        lake.put(source="NSE", dataset=LISTING, business_date=day, filename=f"listing_{day:%Y%m%d}.json",
                 payload=json.dumps(rows).encode(), url=f"{API}?from_date={day:%d-%m-%Y}&to_date={day:%d-%m-%Y}")
        out["days"] += 1
        out["listed"] += len(rows)
        for r in rows:
            url = r.get("xmlFileName") or ""
            if not url.lower().endswith(".xml"):
                continue
            fn = url.rsplit("/", 1)[-1]
            if lake.exists("NSE", DATASET, day, fn):
                continue
            try:
                res = provider.fetch_xbrl(url)
            except FetchError:
                out["xbrl_failed"] += 1
                continue
            lake.put(source="NSE", dataset=DATASET, business_date=day, filename=res.filename, payload=res.payload,
                     url=res.url, content_type=res.content_type, http_status=res.http_status,
                     retrieved_at=res.retrieved_at)
            out["xbrl_stored"] += 1
    return out


COLS = ["app_id", "disclosure", "symbol", "isin", "company", "regulation", "revised", "submission", "disseminated_at",
        "category", "person", "instrument", "side", "qty", "value_inr", "held_before", "pct_before", "held_after",
        "pct_after", "mode", "trade_from", "trade_to", "intimated_on", "exchange", "lake_key"]


def load(con, cfg, start: date, end: date) -> dict:
    """Lake -> ``insider_disclosures``. Idempotent on (app_id, disclosure)."""
    lake = RawLake(cfg.lake)
    out = {"filings": 0, "rows": 0, "no_file": 0, "parse_failed": 0}
    now = datetime.now(timezone.utc)
    for day in _days(max(start, FIRST), end):
        key = f"NSE/{LISTING}/{day:%Y}/{day:%m}/{day:%d}/listing_{day:%Y%m%d}.json"
        if lake.meta(key) is None:
            continue
        for r in json.loads(lake.read(key)):
            fn = (r.get("xmlFileName") or "").rsplit("/", 1)[-1]
            obj = f"NSE/{DATASET}/{day:%Y}/{day:%m}/{day:%d}/{fn}"
            if not fn or lake.meta(obj) is None:
                out["no_file"] += 1
                continue
            try:
                ds = parse(lake.read(obj))
            except ET.ParseError:
                out["parse_failed"] += 1
                continue
            out["filings"] += 1
            when = XR._ts(r.get("exchdisstime")) or XR._ts(r.get("broadcastDateTime"))
            for d in ds:
                row = {**d, "app_id": str(r.get("appId")), "submission": r.get("typeOfSubmission"),
                       "disseminated_at": when, "lake_key": obj, "symbol": d.get("symbol") or r.get("symbol"),
                       "company": d.get("company") or r.get("companyName")}
                con.execute(f"INSERT OR REPLACE INTO insider_disclosures ({', '.join(COLS)}, observed_at) "
                            f"VALUES ({', '.join('?' * len(COLS))}, ?)", [row.get(c) for c in COLS] + [now])
                out["rows"] += 1
    return out
