"""SEBI's public RSS feed (orders, enforcement, circulars, press releases): a forward record from 2026-10-18.

``https://www.sebi.gov.in/sebirss.xml`` (robots.txt allows it) carries the latest items only, so it is read nightly
and every item kept. The section comes from the link path (``/enforcement/orders/``, ``/legal/circulars/``...). An
order naming a listed company is a rare, high-impact negative event; matching titles to companies is left to the
reader of this table - names in titles are free text.
"""
from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone

from ..lake.store import RawLake

URL = "https://www.sebi.gov.in/sebirss.xml"
UA = "Mozilla/5.0 (financial-brain personal research)"


def fetch() -> bytes:
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def section(link: str) -> str:
    parts = [p for p in link.split("sebi.gov.in/", 1)[-1].split("/") if p]
    return "/".join(parts[:2]) if len(parts) >= 2 else (parts[0] if parts else "")


def parse(payload: bytes) -> list[dict]:
    out = []
    for it in ET.fromstring(payload).iter("item"):
        link = (it.findtext("link") or "").strip()
        raw = (it.findtext("pubDate") or "").strip()
        when = None
        for f in ("%d %b, %Y %z", "%d %b %Y %H:%M:%S %z", "%d %b, %Y"):
            try:
                when = datetime.strptime(raw, f).date()
                break
            except ValueError:
                continue
        out.append({"link": link, "title": (it.findtext("title") or "").strip(), "published": when,
                    "section": section(link)})
    return out


def collect(con, cfg, *, fetch_fn=fetch) -> dict:
    body = fetch_fn()
    RawLake(cfg.lake).put(source="SEBI", dataset="rss", business_date=date.today(), filename="sebirss.xml",
                          payload=body, url=URL, content_type="application/xml")
    items = parse(body)
    now = datetime.now(timezone.utc)
    con.executemany("INSERT OR IGNORE INTO sebi_items VALUES (?,?,?,?,?)",
                    [[i["link"], i["title"], i["published"], i["section"], now] for i in items])
    return {"items": len(items)}
