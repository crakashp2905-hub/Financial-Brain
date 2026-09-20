"""News referenced by a filing - extracted deterministically, never fetched (Tier 0).

Roughly 19k filings in the archive are exchange clarifications: "The Exchange has sought
clarification from X with reference to news appeared in ...". The filing text itself is
procedural boilerplate and reads as neutral, but the *news* it is about is the thing
that moved the price - and the filing usually carries it:

    quoted   ... quoting 'Bandhan Bank shares rise up to 4% as lender unveils ...'
    captioned ... captioned 'PVR Inox shares fall 8% amid internal probe into alleged ...'
    in a URL  .../news/business/markets/fssai-initiates-legal-action-against-nestle-
              india-on-baby-formula-shares-fall-nearly-2-14032...html

So the headline is recovered from the bytes we already hold: no request to the
publisher, nothing to re-license, and the result is a deterministic function of the
filing - Tier DERIVED, reproducible, testable. Fetching the article is a separate,
optional step (providers/moneycontrol.py) for the cases where only a bare link exists.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# "quoting <headline>", "captioned '<headline>'", "titled "<headline>"", "news item ... :"
QUOTED = [
    re.compile(r"(?:quoting|captioned|titled|headlined)\s*[:\-]?\s*[\"'‘“]([^\"'’”]{15,300})", re.I),
    re.compile(r"(?:quoting|captioned|titled|headlined)\s+([A-Z][^.<]{20,300}?)(?:\.|<BR>|$)"),
]
URL = re.compile(r"https?://[^\s\"'<>)\]]+", re.I)
BARE = re.compile(r"\b(?:www\.)([a-z0-9-]+\.(?:com|in|net|org)(?:\.[a-z]{2})?)\b", re.I)
# A slug segment: words joined by hyphens, usually ending in a numeric article id.
SLUG = re.compile(r"/([a-z0-9]+(?:-[a-z0-9]+){4,})(?:[-_.]|\.html|$)", re.I)
TRAIL_ID = re.compile(r"(?:-\d{4,})+$")
STOP = {"www", "com", "in", "html", "amp"}


def _clean(text: str) -> str:
    return " ".join(text.replace("<BR>", " ").split()).strip(" -–:;,")


def slug_headline(url: str) -> str | None:
    """The headline a news URL carries in its path, as plain words. None when the path
    has no article slug (a bare domain link, a section page)."""
    path = urlparse(url).path
    best = None
    for m in SLUG.finditer(path):
        seg = TRAIL_ID.sub("", m.group(1))
        words = [w for w in seg.split("-") if w and w.lower() not in STOP]
        if len(words) >= 5 and (best is None or len(words) > len(best)):
            best = words
    if not best:
        return None
    head = " ".join(best)
    return head[0].upper() + head[1:]


def extract(text: str) -> dict | None:
    """The news a filing refers to: {url, domain, headline, how}. None when the filing
    references no external news. ``how`` records which rule produced the headline, so a
    claim built on it can say where it came from."""
    if not text:
        return None
    raw = _clean(text)
    urls = [u.rstrip(".,;)’\"'") for u in URL.findall(raw)]
    external = [u for u in urls if "bseindia.com" not in u.lower()
                and "nseindia.com" not in u.lower()]
    # A filing often carries both a bare publisher link and the article link; the one
    # with an article slug is the informative one, whichever came first in the text.
    external.sort(key=lambda u: (slug_headline(u) is not None, len(u)), reverse=True)
    url = external[0] if external else None
    domain = urlparse(url).netloc.lower().removeprefix("www.") if url else None
    if not domain:
        m = BARE.search(raw)                 # "www.moneycontrol.com" written without a scheme
        domain = m.group(1).lower() if m else None

    for pat in QUOTED:
        m = pat.search(raw)
        if m:
            head = _clean(m.group(1))
            if head and not head.lower().startswith("http"):
                return {"url": url, "domain": domain, "headline": head, "how": "quoted"}
    if url:
        head = slug_headline(url)
        if head:
            return {"url": url, "domain": domain, "headline": head, "how": "url_slug"}
        return {"url": url, "domain": domain, "headline": None, "how": "link_only"}
    return None


def extract_day(con, d, *, refresh: bool = False) -> dict:
    """Store the news reference for each of a day's filings that has one."""
    from datetime import datetime, timezone
    where = "" if refresh else "AND n.news_id IS NULL"
    rows = con.execute(f"""
        SELECT a.news_id, a.headline, a.subject FROM announcements a
        LEFT JOIN announcement_news n ON n.news_id = a.news_id
        WHERE a.business_date = ? {where}""", [d]).fetchall()
    found = 0
    for nid, head, subj in rows:
        r = extract(f"{head or ''} {subj or ''}")
        if not r:
            continue
        con.execute("DELETE FROM announcement_news WHERE news_id = ?", [nid])
        con.execute("""INSERT INTO announcement_news (news_id, url, domain, headline, how,
                       extracted_at) VALUES (?,?,?,?,?,?)""",
                    [nid, r["url"], r["domain"], r["headline"], r["how"],
                     datetime.now(timezone.utc)])
        found += 1
    return {"scanned": len(rows), "with_news_reference": found}
