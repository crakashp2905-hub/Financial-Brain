"""Read the document a filing points at, and mint what it says (C11).

Scope is deliberate: high-materiality filings, newest first, a handful per run. The
archive holds 2.78M attachments and fetching them all would be both rude and pointless -
the value is in the filings a reader is about to see, where the headline withholds the
number ("Receipt of order", "Please refer the Enclosed files").

Each document is stored in the lake before it is parsed, so any claim can be replayed
from the exact bytes. Facts are Tier 1: this is the company's own disclosure, published
by the exchange, not a compiler's summary of it.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from ..docintel import extract
from ..evidence import ledger
from ..lake.store import RawLake
from ..providers.base import FetchError
from ..providers.bse_filing import BSEFiling
from .corpact_feed import _register

UNITS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}
LABEL = {"order_value": "order value", "penalty": "penalty", "tax_demand": "tax demand",
         "fund_raise": "amount being raised", "acquisition": "deal value",
         "default": "amount in default", "amount": "amount",
         "dividend_per_share": "dividend per share",
         "demand_set_aside": "demand set aside (relief)"}


def _money(kind: str, value: float, unit: str) -> str:
    if unit == "INR_per_share":
        return f"{LABEL[kind]} ₹{value:,.2f}"
    symbol = UNITS.get(unit, unit + " ")
    if unit == "INR" and value >= 10 ** 7:
        return f"{LABEL.get(kind, kind)} {symbol}{value / 10 ** 7:,.2f} crore"
    if unit == "INR" and value >= 10 ** 5:
        return f"{LABEL.get(kind, kind)} {symbol}{value / 10 ** 5:,.2f} lakh"
    return f"{LABEL.get(kind, kind)} {symbol}{value:,.0f}"


def read_day(con, cfg, d: date, *, limit: int = 15, provider=None,
             materiality: tuple[str, ...] = ("high",)) -> dict:
    provider = provider or BSEFiling()
    lake = RawLake(cfg.lake)
    marks = ",".join("?" * len(materiality))
    rows = con.execute(f"""
        SELECT a.news_id, a.attachment, a.company, a.isin, a.published_at, a.event_type
        FROM announcements a
        LEFT JOIN filing_documents f ON f.news_id = a.news_id
        WHERE a.business_date = ? AND a.materiality IN ({marks})
          AND a.attachment IS NOT NULL AND a.attachment <> '' AND f.news_id IS NULL
        ORDER BY a.published_at DESC""", [d, *materiality]).fetchall()[:limit]
    stats = {"candidates": len(rows), "read": 0, "failed": 0, "scanned_no_text": 0,
             "facts": 0}
    now = datetime.now(timezone.utc)
    for news_id, url, company, isin, published, event_type in rows:
        try:
            res = provider.fetch_url(url)
        except FetchError as e:
            stats["failed"] += 1
            con.execute("""INSERT INTO filing_documents (news_id, url, pages, chars,
                           status, fetched_at) VALUES (?,?,?,?,?,?)
                           ON CONFLICT (news_id) DO NOTHING""",
                        [news_id, url, 0, 0, f"fetch failed: {str(e)[:120]}", now])
            continue
        obj = lake.put(source="FILING", dataset="attachment", business_date=d,
                       filename=res.filename, payload=res.payload, url=res.url,
                       content_type=res.content_type, http_status=res.http_status,
                       retrieved_at=res.retrieved_at)
        _register(con, obj)
        try:
            text, pages = extract.text_of(res.payload)
        except Exception as e:             # noqa: BLE001 - a malformed PDF is a finding
            stats["failed"] += 1
            con.execute("""INSERT INTO filing_documents (news_id, url, pages, chars,
                           status, lake_key, fetched_at) VALUES (?,?,?,?,?,?,?)
                           ON CONFLICT (news_id) DO NOTHING""",
                        [news_id, url, 0, 0, f"unreadable: {type(e).__name__}", obj.key, now])
            continue
        scanned = extract.looks_scanned(text, pages)
        status = "scanned (no text layer)" if scanned else "ok"
        stats["scanned_no_text"] += scanned
        con.execute("""INSERT INTO filing_documents (news_id, url, pages, chars, status,
                       lake_key, fetched_at) VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT (news_id) DO NOTHING""",
                    [news_id, url, pages, len(text), status, obj.key, now])
        stats["read"] += 1
        if scanned:
            continue
        for f in extract.facts(text, event_type=event_type):
            eid = ledger.mint(
                con, kind="filing_fact", subject=isin or company,
                as_of=published or datetime.combine(d, datetime.min.time()),
                claim=f"{company}: {_money(f.kind, f.value, f.unit)} ({f.raw})",
                value={"news_id": news_id, "fact": f.kind, "value": f.value,
                       "unit": f.unit, "raw": f.raw, "context": f.context,
                       "event_type": event_type},
                source="FILING", source_tier=1, lake_key=obj.key,
                derivation="docintel/extract amounts", published_at=published)
            con.execute("""INSERT INTO filing_facts (news_id, kind, value, unit, raw,
                           context, evidence_id, extracted_at) VALUES (?,?,?,?,?,?,?,?)
                           ON CONFLICT (news_id, kind) DO NOTHING""",
                        [news_id, f.kind, f.value, f.unit, f.raw, f.context, eid, now])
            stats["facts"] += 1
    return stats
