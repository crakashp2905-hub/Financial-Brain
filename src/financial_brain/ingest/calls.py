"""Ingest earnings-call transcripts and investor presentations (C11).

Reads the attachment, keeps its structure and an embedding for retrieval, and mints one
modest Tier-1 claim: this company held a call, the transcript runs this long, the Q&A is
present or it is not. No reading of what was *said* - that needs a labelled set of call
text, which does not exist yet (ADR-0003).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from ..docintel import calls as parse
from ..evidence import ledger
from ..lake.store import RawLake
from ..llm.backends import BackendUnavailable
from ..providers.base import FetchError
from ..providers.bse_filing import BSEFiling
from .corpact_feed import _register

EVENTS = ("EARNINGS_CALL", "INVESTOR_PRESENTATION")


def read_day(con, cfg, d: date, *, limit: int = 10, provider=None,
             embed: bool = True) -> dict:
    provider = provider or BSEFiling()
    lake = RawLake(cfg.lake)
    marks = ",".join("?" * len(EVENTS))
    rows = con.execute(f"""
        SELECT a.news_id, a.attachment, a.company, a.isin, a.published_at, a.event_type
        FROM announcements a
        LEFT JOIN call_documents c ON c.news_id = a.news_id
        WHERE a.business_date = ? AND a.event_type IN ({marks})
          AND a.attachment IS NOT NULL AND a.attachment <> '' AND c.news_id IS NULL
        ORDER BY a.published_at DESC""", [d, *EVENTS]).fetchall()[:limit]
    stats = {"candidates": len(rows), "read": 0, "with_qa": 0, "scanned": 0,
             "embedded": 0, "failed": 0}
    now = datetime.now(timezone.utc)
    for news_id, url, company, isin, published, event_type in rows:
        try:
            res = provider.fetch_url(url)
            doc = parse.read(res.payload)
        except (FetchError, Exception):        # noqa: BLE001 - one document, not the run
            stats["failed"] += 1
            continue
        obj = lake.put(source="FILING", dataset="call", business_date=d,
                       filename=res.filename, payload=res.payload, url=res.url,
                       content_type=res.content_type, http_status=res.http_status,
                       retrieved_at=res.retrieved_at)
        _register(con, obj)

        vector = None
        if embed and not doc["scanned"]:
            try:
                vector = json.dumps(parse.embed(doc["text"]))
                stats["embedded"] += 1
            except BackendUnavailable:
                vector = None                  # retrieval is optional; the text is not
        con.execute("""INSERT INTO call_documents (news_id, isin, company, called_on,
                       event_type, pages, chars, commentary_chars, qa_chars, has_qa,
                       speakers, embedding, lake_key, fetched_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT (news_id) DO NOTHING""",
                    [news_id, isin, company, d, event_type, doc["pages"], doc["chars"],
                     doc["commentary_chars"], doc["qa_chars"], doc["has_qa"],
                     json.dumps(doc["speakers"]), vector, obj.key, now])
        stats["read"] += 1
        stats["with_qa"] += doc["has_qa"]
        stats["scanned"] += doc["scanned"]
        if doc["scanned"]:
            continue
        ledger.mint(
            con, kind="earnings_call", subject=isin or company,
            as_of=published or datetime.combine(d, datetime.min.time()),
            claim=(f"{company}: {event_type.replace('_', ' ').lower()} document, "
                   f"{doc['pages']} pages"
                   + (f", Q&A present ({doc['qa_chars']:,} characters)" if doc["has_qa"]
                      else ", no Q&A section")),
            value={"news_id": news_id, "pages": doc["pages"], "chars": doc["chars"],
                   "has_qa": doc["has_qa"], "speakers": doc["speakers"][:8]},
            source="FILING", source_tier=1, lake_key=obj.key,
            derivation="docintel/calls", published_at=published)
    return stats
