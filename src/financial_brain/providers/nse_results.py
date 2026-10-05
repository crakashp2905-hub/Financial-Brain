"""NSE's list of a company's financial-results filings, and the XBRL file each one carries (Tier 1).

Two read-only GETs, both against the exchange's own public disclosure surface:

* ``/api/corporates-financial-results?index=equities&symbol=X&period=Quarterly`` - one row per filing
  with the period, the basis (consolidated or standalone), the exchange's dissemination timestamp and a
  link to the XBRL instance. The endpoint wants the cookies the site's own page sets, so a session is
  warmed first and re-warmed when it goes stale.
* the XBRL instance itself on ``nsearchives.nseindia.com``.

The XBRL is what the company filed in the exchange's own taxonomy, with the values already tagged. That
is why this replaces parsing results PDFs: no OCR, no layout, no guessing which column is this quarter.

Politeness: one request at a time, ``MIN_INTERVAL`` apart, GET only, an allowlist of NSE hosts, and
nothing is retried in a tight loop. NSE's robots.txt allows these paths.
"""
from __future__ import annotations

import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse

from .base import USER_AGENT, FetchError, FetchResult, Provider

HOME = "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"
API = "https://www.nseindia.com/api/corporates-financial-results"
HOSTS = ("www.nseindia.com", "nsearchives.nseindia.com", "archives.nseindia.com")
MIN_INTERVAL = 0.5
TIMEOUT = 30
SESSION_TTL = 600.0


class NSEResults(Provider):
    source = "NSE"
    dataset = "xbrl_results"
    tier = 1

    def __init__(self, *, min_interval: float = MIN_INTERVAL):
        self.min_interval = min_interval
        self._last = 0.0
        self._warmed = 0.0
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar))

    def fetch(self, business_date) -> FetchResult:          # Provider interface
        raise NotImplementedError("results are fetched per symbol and per URL, not per date")

    # ------------------------------------------------------------------ plumbing
    def _get(self, url: str) -> tuple[bytes, int, str]:
        host = urlparse(url).netloc.lower()
        if host not in HOSTS:
            raise FetchError(f"not an NSE host: {host}", retryable=False, status=400)
        # The static archive host serves files without a session; only the JSON API needs the cookies the
        # site's own page sets. Using no cookie jar for downloads means a stale session cannot stall them.
        needs_session = host == "www.nseindia.com"
        last: Exception | None = None
        for attempt in range(3):
            if needs_session and time.monotonic() - self._warmed > SESSION_TTL:
                try:
                    self._warm()
                except OSError as e:               # DNS failure, no route, timeout: a failed attempt, not a crash
                    last = e
                    time.sleep(1.5 * (attempt + 1))
                    continue
            gap = time.monotonic() - self._last
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            self._last = time.monotonic()
            req = urllib.request.Request(url, method="GET", headers={
                "User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
                "Referer": HOME})
            try:
                opener = self._opener if needs_session else urllib.request.build_opener()
                with opener.open(req, timeout=TIMEOUT) as r:
                    return r.read(), r.status, r.headers.get("Content-Type", "")
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise FetchError(f"HTTP 404 for {url}", retryable=False, status=404) from e
                last = e
            except OSError as e:                      # includes timeouts and resets
                last = e
            if needs_session:
                self._jar.clear()                     # a failed session is rebuilt, not reused
                self._warmed = 0.0
            time.sleep(1.5 * (attempt + 1))
        raise FetchError(f"failed after 3 attempts: {last}", retryable=True)

    def _warm(self) -> None:
        req = urllib.request.Request(HOME, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
        with self._opener.open(req, timeout=60) as r:
            r.read(1024)
        self._warmed = time.monotonic()
        self._last = time.monotonic()

    # ------------------------------------------------------------------ public reads
    def filings(self, symbol: str, *, period: str = "Quarterly") -> list[dict]:
        """Every results filing NSE lists for ``symbol``, newest first. Empty if it lists none."""
        q = urllib.parse.urlencode({"index": "equities", "symbol": symbol, "period": period})
        body, _, _ = self._get(f"{API}?{q}")
        try:
            data = json.loads(body)
        except ValueError as e:
            raise FetchError(f"results list for {symbol!r} is not JSON", retryable=True) from e
        if not isinstance(data, list):
            raise FetchError(f"results list for {symbol!r} is {type(data).__name__}, not a list",
                             retryable=True)
        return data

    @staticmethod
    def has_xbrl(row: dict) -> bool:
        """Filings before the XBRL era carry a placeholder link ending in ``/-``."""
        x = row.get("xbrl") or ""
        return x.startswith("http") and not x.endswith("/-") and x.lower().endswith(".xml")

    def fetch_xbrl(self, url: str) -> FetchResult:
        body, status, ctype = self._get(url)
        if b"<xbrli:xbrl" not in body[:4000] and b"xbrl" not in body[:4000].lower():
            raise FetchError(f"not an XBRL instance ({len(body)} bytes): {url}", retryable=False,
                             status=status)
        return FetchResult(payload=body, url=url, retrieved_at=self._now(),
                           filename=url.rsplit("/", 1)[-1], content_type=ctype or "application/xml",
                           http_status=status)
