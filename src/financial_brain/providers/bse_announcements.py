"""BSE corporate announcements (Tier 1) - one lake object per day.

``api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w`` serves 50 announcements a
page for a date window and needs no browser session. A day runs from a few hundred to
~2,000 announcements (2026-08-05: 2,071, 42 pages).

The day's pages are stored together, each page's body kept verbatim, so the lake holds
exactly what BSE served and a day can be re-parsed without the network.

Quirks handled (both observed):
* the page count must come from ``Table1.ROWCNT`` - older responses have no
  ``TotalPageCnt``;
* field names vary in case across years (``BSENEWSID`` / ``BSENewsid``), so rows are
  read case-insensitively.

Timestamps are IST wall-clock as BSE publishes them. Three are kept - submitted by the
company, disseminated by the exchange, and the news timestamp - because a decision may
only use an announcement from the moment it became public (ARCHITECTURE.md §5.2).
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime

from .base import FetchError, FetchResult, NotPublished, Provider

URL = ("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?pageno={p}"
       "&strCat=-1&strPrevDate={d}&strScrip=&strSearch=P&strToDate={d}&strType=C"
       "&subcategory=-1")
HEADERS = {"Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com",
           "Accept": "application/json, text/plain, */*"}
PAGE_SIZE = 50
ATTACH_BASE = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"


def _ts(v) -> datetime | None:
    s = (v or "").strip() if isinstance(v, str) else ""
    if not s:
        return None
    s = s.split(".")[0]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


class BSEAnnouncementsProvider(Provider):
    source = "BSE"
    dataset = "announcements"
    tier = 1

    def _page(self, d: date, p: int) -> bytes:
        payload, _, _ = self._http_get(URL.format(p=p, d=d.strftime("%Y%m%d")),
                                       headers=HEADERS)
        if not payload.lstrip().startswith(b"{"):
            raise FetchError(f"BSE announcements {d} page {p}: not JSON", retryable=True)
        return payload

    def fetch(self, business_date: date) -> FetchResult:
        first = self._page(business_date, 1)
        body = json.loads(first)
        count = int(((body.get("Table1") or [{}])[0] or {}).get("ROWCNT") or 0)
        if count == 0:
            raise NotPublished(f"BSE published no announcements on {business_date}")
        pages = [first.decode("utf-8", errors="replace")]
        for p in range(2, math.ceil(count / PAGE_SIZE) + 1):
            pages.append(self._page(business_date, p).decode("utf-8", errors="replace"))
        payload = json.dumps({"date": business_date.isoformat(), "rowcount": count,
                              "pages": pages}).encode()
        return FetchResult(payload=payload, url=URL.format(p="*", d=business_date.strftime("%Y%m%d")),
                           retrieved_at=self._now(),
                           filename=f"bse_announcements_{business_date:%Y%m%d}.json",
                           content_type="application/json")

    @staticmethod
    def parse(payload: bytes) -> tuple[int, list[dict]]:
        """Return ``(rowcount BSE declared, rows)``, one dict per announcement."""
        day = json.loads(payload)
        rows, seen = [], set()
        for page in day["pages"]:
            for raw in json.loads(page).get("Table") or []:
                r = {k.upper(): v for k, v in raw.items()}
                nid = str(r.get("NEWSID") or "").strip()
                if not nid or nid in seen:
                    continue
                seen.add(nid)
                att = (r.get("ATTACHMENTNAME") or "").strip()
                rows.append({
                    "news_id": nid,
                    "scrip_code": str(r.get("SCRIP_CD") or "").strip(),
                    "company": (r.get("SLONGNAME") or "").strip(),
                    "category": (r.get("CATEGORYNAME") or "").strip(),
                    "subcategory": (r.get("SUBCATNAME") or "").strip(),
                    "headline": (r.get("HEADLINE") or "").strip(),
                    "subject": (r.get("NEWSSUB") or "").strip(),
                    "critical": bool(r.get("CRITICALNEWS")),
                    "submitted_at": _ts(r.get("NEWS_SUBMISSION_DT")),
                    "published_at": _ts(r.get("DISSEMDT")) or _ts(r.get("NEWS_DT")),
                    "news_at": _ts(r.get("NEWS_DT")),
                    "attachment": ATTACH_BASE + att if att else None,
                })
        return int(day.get("rowcount") or 0), rows
