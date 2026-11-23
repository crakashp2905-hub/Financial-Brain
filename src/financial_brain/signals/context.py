"""As-of event/news evidence join for read-only signal generation."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


class ContextError(ValueError):
    pass


def read_only_context(con, *, isin: str, as_of: datetime) -> dict:
    """Join scanner identity to observable company events/news known by a decision cutoff.

    ``con`` is expected to be opened read-only by the caller. This function only reads
    announcements, linked article metadata and lake hashes; its return has no order API.
    ``published_at`` columns in those source tables are local IST wall-clock timestamps.
    """
    if not isin.strip() or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ContextError("ISIN and timezone-aware as_of are required")
    cutoff = as_of.astimezone(IST).replace(tzinfo=None)
    events = con.execute("""SELECT a.news_id, a.event_type, a.materiality, a.headline,
                                  a.subject, COALESCE(a.published_at, a.submitted_at) AS source_time,
                                  a.observed_at, l.sha256 AS source_sha256
                           FROM announcements a LEFT JOIN lake_manifest l ON l.key=a.evidence_key
                           WHERE a.isin=? AND COALESCE(a.published_at, a.submitted_at) IS NOT NULL
                             AND COALESCE(a.published_at, a.submitted_at) <= ?
                             AND a.observed_at <= ?
                           ORDER BY source_time, a.news_id""",
                        [isin, cutoff, as_of]).fetchall()
    event_rows = []
    news_rows = []
    for row in events:
        news_id, event_type, materiality, headline, subject, source_time, observed, digest = row
        event = {"news_id": news_id, "event_type": event_type, "materiality": materiality,
                 "headline": headline, "subject": subject, "source_time": source_time,
                 "observed_at": observed, "source_sha256": digest}
        event_rows.append(event)
        linked = con.execute("""SELECT n.url, n.headline AS linked_headline, n.how,
                                      ar.title, ar.published_at, ar.fetched_at, lm.sha256
                               FROM announcement_news n
                               LEFT JOIN news_articles ar ON ar.url=n.url
                               LEFT JOIN lake_manifest lm ON lm.key=ar.lake_key
                               WHERE n.news_id=? AND n.extracted_at <= ?
                               ORDER BY n.url""", [news_id, as_of]).fetchall()
        for article in linked:
            url, linked_headline, how, title, published, fetched, article_hash = article
            if fetched is None or fetched > as_of:
                continue
            if published is not None and published > cutoff:
                continue
            news_rows.append({"news_id": news_id, "url": url, "headline": linked_headline,
                              "title": title, "how": how, "published_at": published,
                              "fetched_at": fetched,
                              "availability": "PUBLICATION_TIME_UNKNOWN" if published is None
                              else "PUBLISHED_BEFORE_CUTOFF",
                              "source_sha256": article_hash})
    body = {"isin": isin, "as_of": as_of.isoformat(), "events": event_rows, "news": news_rows,
            "execution_allowed": False}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str,
                           allow_nan=False)
    return {**body, "context_sha256": hashlib.sha256(canonical.encode()).hexdigest()}


def research_packet(con, candidate: dict, *, as_of: datetime) -> dict:
    """Attach known event/news evidence to a scanner row; never synthesize a trade action."""
    if not isinstance(candidate, dict) or not candidate.get("ticker") or not candidate.get("isin"):
        raise ContextError("scanner candidate requires ticker and ISIN")
    context = read_only_context(con, isin=str(candidate["isin"]), as_of=as_of)
    packet = {"candidate": dict(candidate), "context": context,
              "decision_mode": "READ_ONLY_RESEARCH", "execution_allowed": False}
    packet["packet_sha256"] = hashlib.sha256(json.dumps(
        packet, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
    ).encode()).hexdigest()
    return packet
