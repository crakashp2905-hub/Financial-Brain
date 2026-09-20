"""Fetch the article a filing links to, when the publisher allows it (Tier 3).

Only URLs already present in ``announcement_news`` are fetched - the filing pointed at
them - and only from allowed hosts, one at a time. A fetched title is also written back
as the filing's headline when extraction could not recover one (``how='link_only'``),
which is where fetching earns its place: the filing says "see this link" and nothing
else, so the link is the only way to know what the news was.

Refusals are recorded as refusals (status 0 with the reason) rather than retried.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from ..config import TIER
from ..evidence import ledger
from ..lake.store import RawLake
from ..providers.news_article import NewsArticle, NotAllowed, parse_article
from .corpact_feed import _register

MIN_SEGMENTS = 2        # ".../news/business/<slug>.html" is an article; "/" is a home page


def fetch_day(con, cfg, d: date, *, limit: int = 20, provider=None,
              only_missing_headline: bool = False) -> dict:
    provider = provider or NewsArticle()
    lake = RawLake(cfg.lake)
    where = "AND n.how = 'link_only'" if only_missing_headline else ""
    rows = con.execute(f"""
        SELECT n.news_id, n.url, n.domain, n.how, a.company FROM announcement_news n
        JOIN announcements a USING (news_id)
        LEFT JOIN news_articles ar ON ar.url = n.url
        WHERE a.business_date = ? AND n.url IS NOT NULL AND ar.url IS NULL {where}
        ORDER BY a.published_at""", [d]).fetchall()[:limit]
    stats = {"candidates": len(rows), "fetched": 0, "refused": 0, "failed": 0,
             "not_an_article": 0, "headlines_recovered": 0}
    now = datetime.now(timezone.utc)
    for news_id, url, domain, how, company in rows:
        # A filing that links only to the publisher's front page points at whatever that
        # page shows today, not at the news it meant. Fetching it would yield the site's
        # own title ("Business News: Stock and Share Market News...") - not a headline.
        if not is_article_url(url):
            stats["not_an_article"] += 1
            continue
        try:
            res = provider.fetch_url(url)
        except NotAllowed as e:
            stats["refused"] += 1
            con.execute("""INSERT INTO news_articles (url, domain, http_status, fetched_at,
                           excerpt) VALUES (?,?,?,?,?) ON CONFLICT (url) DO NOTHING""",
                        [url, domain or "", 0, now, f"not fetched: {e}"])
            continue
        except Exception as e:                       # noqa: BLE001 - network reality
            stats["failed"] += 1
            con.execute("""INSERT INTO news_articles (url, domain, http_status, fetched_at,
                           excerpt) VALUES (?,?,?,?,?) ON CONFLICT (url) DO NOTHING""",
                        [url, domain or "", -1, now, f"fetch failed: {type(e).__name__}"])
            continue
        obj = lake.put(source="MONEYCONTROL" if domain == "moneycontrol.com" else "NEWS",
                       dataset="article", business_date=d, filename=res.filename,
                       payload=res.payload, url=res.url, content_type=res.content_type,
                       http_status=res.http_status, retrieved_at=res.retrieved_at)
        _register(con, obj)
        art = parse_article(res.payload)
        con.execute("""INSERT INTO news_articles (url, domain, title, published_at,
                       excerpt, http_status, lake_key, fetched_at)
                       VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (url) DO NOTHING""",
                    [url, domain or "", art["title"], art["published_at"], art["excerpt"],
                     res.http_status, obj.key, now])
        stats["fetched"] += 1
        if art["title"]:
            source = "MONEYCONTROL" if domain == "moneycontrol.com" else "NEWS"
            ledger.mint(con, kind="news_article", subject=company,
                        as_of=art["published_at"] or datetime.combine(d, datetime.min.time()),
                        claim=f"{domain} reported: {art['title']}",
                        value={"url": url, "title": art["title"],
                               "excerpt": (art["excerpt"] or "")[:400]},
                        source=source, source_tier=TIER.get(source, 3),
                        lake_key=obj.key, derivation="providers/news_article og:title",
                        published_at=art["published_at"])
            if how == "link_only":
                con.execute("""UPDATE announcement_news SET headline = ?, how = 'fetched'
                               WHERE news_id = ? AND headline IS NULL""",
                            [art["title"], news_id])
                stats["headlines_recovered"] += 1
    return stats


def is_article_url(url: str) -> bool:
    """Does this URL point at an article, rather than a home or section page?"""
    from urllib.parse import urlparse
    path = urlparse(url).path.strip("/")
    if not path:
        return False
    return path.endswith(".html") or len([p for p in path.split("/") if p]) >= MIN_SEGMENTS
