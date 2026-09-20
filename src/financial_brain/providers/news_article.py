"""Fetch one news article, politely (Tier 3).

This is deliberately *not* a crawler. It fetches a specific URL that a filing already
pointed at, one at a time, with a minimum gap between requests, and only from hosts the
owner has allowed (``ALLOWED`` - Moneycontrol today). Before the first request to a host
its robots.txt is read and obeyed: Moneycontrol allows news paths while disallowing
``/stocks/company_info/``, ``/financials/results/`` and ``/news/printpage/``, so those
are refused here rather than requested and discarded.

Only the title, publication time and the publisher's own summary (og:description) are
kept. The article body is not stored: we need to know *what the news said*, not to hold
a copy of someone's copy.
"""
from __future__ import annotations

import html
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import urlparse

from .base import FetchError, FetchResult, Provider
from .robots import Robots

ALLOWED = {"moneycontrol.com"}          # hosts the owner has authorised
MIN_INTERVAL = 3.0                      # seconds between requests to the same host

META = {
    "title": [r'<meta\s+property="og:title"\s+content="([^"]{3,400})"',
              r"<title>([^<]{3,400})</title>"],
    "excerpt": [r'<meta\s+property="og:description"\s+content="([^"]{3,600})"',
                r'<meta\s+name="description"\s+content="([^"]{3,600})"'],
    "published": [r'article:published_time"\s+content="([^"]+)"',
                  r'"datePublished"\s*:\s*"([^"]+)"'],
}
SUFFIX = re.compile(r"\s*[-|]\s*Moneycontrol(?:\.com)?\s*$", re.I)


class NotAllowed(FetchError):
    """The host is not on the allowlist, or its robots.txt disallows this path."""

    def __init__(self, message: str):
        super().__init__(message, retryable=False, status=403)


class NewsArticle(Provider):
    source = "NEWS"
    dataset = "article"
    tier = 3

    def __init__(self, *, allowed: set[str] | None = None, min_interval: float = MIN_INTERVAL):
        self.allowed = allowed if allowed is not None else ALLOWED
        self.min_interval = min_interval
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = {}

    # -- politeness ----------------------------------------------------------
    def host_of(self, url: str) -> str:
        return urlparse(url).netloc.lower().removeprefix("www.")

    def _rules(self, url: str):
        host = urlparse(url).netloc
        if host not in self._robots:
            u = f"{urlparse(url).scheme}://{host}/robots.txt"
            try:
                req = urllib.request.Request(u, headers={"User-Agent": self.USER_AGENT})
                with urllib.request.urlopen(req, timeout=20) as r:
                    body = r.read().decode("utf-8", errors="replace")
                self._robots[host] = Robots(body, self.USER_AGENT)
            except (urllib.error.URLError, OSError):
                self._robots[host] = None     # unreadable: treat as "do not fetch"
        return self._robots[host]

    def may_fetch(self, url: str) -> tuple[bool, str]:
        """(allowed, why). Both the owner's allowlist and the publisher's robots.txt
        have to say yes."""
        if self.host_of(url) not in self.allowed:
            return False, f"{self.host_of(url)} is not on the allowlist"
        rules = self._rules(url)
        if rules is None:
            return False, "robots.txt could not be read"
        if not rules.can_fetch(url):
            return False, "robots.txt disallows this path"
        return True, "allowed"

    USER_AGENT = "Mozilla/5.0 (compatible; financial-brain/1.0; personal research)"

    def _wait(self, url: str) -> None:
        host = self.host_of(url)
        gap = time.monotonic() - self._last.get(host, 0.0)
        delay = max(self._rules(url).crawl_delay or 0, self.min_interval)
        if gap < delay:
            time.sleep(delay - gap)
        self._last[host] = time.monotonic()

    # -- fetch ---------------------------------------------------------------
    def fetch(self, business_date) -> FetchResult:            # Provider interface
        raise NotImplementedError("news articles are fetched by URL, not by date")

    def fetch_url(self, url: str) -> FetchResult:
        ok, why = self.may_fetch(url)
        if not ok:
            raise NotAllowed(f"refusing {url}: {why}")
        self._wait(url)
        payload, status, ctype = self._http_get(url, timeout=45, retries=2,
                                                headers={"User-Agent": self.USER_AGENT})
        return FetchResult(payload=payload, url=url, retrieved_at=self._now(),
                           filename=urlparse(url).path.strip("/").replace("/", "_")[-120:]
                                    or "article.html",
                           content_type=ctype, http_status=status)


def _first(patterns, text: str) -> str | None:
    for p in patterns:
        m = re.search(p, text, re.I | re.S)
        if m:
            return html.unescape(m.group(1)).strip()
    return None


def parse_article(payload: bytes) -> dict:
    """Title, publication time and the publisher's own summary - nothing more."""
    text = payload.decode("utf-8", errors="replace")
    title = _first(META["title"], text)
    published = _first(META["published"], text)
    when = None
    if published:
        try:
            when = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            when = None
    return {"title": SUFFIX.sub("", title) if title else None,
            "excerpt": _first(META["excerpt"], text), "published_at": when}
