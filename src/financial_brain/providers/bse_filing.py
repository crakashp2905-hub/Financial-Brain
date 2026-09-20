"""The PDF a BSE filing points at (Tier 1).

The announcement row already carries the attachment URL - the exchange published it as
part of the disclosure - so this fetches the document the filing itself names, one at a
time. BSE serves these only with a browser-ish Referer, and returns an HTML error page
rather than a 404 when a link has gone stale, so both are checked here: a payload that
is not a PDF is a failure, not a document.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

from .base import FetchError, FetchResult, Provider

REFERER = "https://www.bseindia.com/corporates/ann.html"
MIN_INTERVAL = 1.0          # seconds between attachment fetches
PDF_MAGIC = b"%PDF"


class BSEFiling(Provider):
    source = "FILING"
    dataset = "attachment"
    tier = 1

    def __init__(self, *, min_interval: float = MIN_INTERVAL):
        self.min_interval = min_interval
        self._last = 0.0

    def fetch(self, business_date) -> FetchResult:          # Provider interface
        raise NotImplementedError("filings are fetched by URL, not by date")

    def candidates(self, url: str) -> list[str]:
        """The URL as filed, then where BSE moves it later.

        Announcements store the ``AttachLive`` path that was correct on the day. Older
        documents are moved to ``AttachHis`` and the live path then 404s: 11 of 20
        attachments in a sample failed for this reason alone, which looked like a broken
        fetcher and was really a stale path.
        """
        urls = [url]
        for a, b in (("AttachLive", "AttachHis"), ("AttachHis", "AttachLive")):
            if a in url:
                urls.append(url.replace(a, b))
        return urls

    def fetch_url(self, url: str) -> FetchResult:
        host = urlparse(url).netloc.lower()
        if not host.endswith("bseindia.com"):
            raise FetchError(f"not a BSE attachment: {url}", retryable=False, status=400)
        last: Exception | None = None
        for candidate in self.candidates(url):
            gap = time.monotonic() - self._last
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            self._last = time.monotonic()
            try:
                payload, status, ctype = self._http_get(
                    candidate, timeout=60, retries=1,
                    headers={"Referer": REFERER, "Accept": "application/pdf,*/*"})
            except FetchError as e:
                last = e
                continue
            if not payload.startswith(PDF_MAGIC):
                # A dead link returns the site's HTML, which would otherwise be stored
                # and parsed as if it were the disclosure.
                last = FetchError(f"not a PDF ({ctype}, {len(payload)} bytes): {candidate}",
                                  retryable=False, status=status)
                continue
            name = urlparse(candidate).path.rsplit("/", 1)[-1] or "attachment.pdf"
            return FetchResult(payload=payload, url=candidate, retrieved_at=self._now(),
                               filename=name, content_type="application/pdf",
                               http_status=status)
        raise last or FetchError(f"no attachment at {url}", retryable=False, status=404)
