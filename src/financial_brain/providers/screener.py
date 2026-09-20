"""Screener.in - a company's headline ratios (Tier 3).

Screener publishes a compact summary per company (market cap, price, P/E, book value,
dividend yield, ROCE, ROE, face value). We read that block and nothing else: its
robots.txt allows ``/company/<SYMBOL>/`` while disallowing user pages, query-sorted
listings and ``/company/source/quarter/*``, and those are refused here rather than
requested.

Tier 3: Screener is a compiler of company filings, not the filer. A ratio from here
never outranks the same number computed from Tier-1 data - it is a cross-check and a
gap-filler, and the evidence row says so.
"""
from __future__ import annotations

import html
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .base import FetchError, FetchResult, Provider
from .robots import Robots

BASE = "https://www.screener.in/company/{symbol}/"
CONSOLIDATED = "https://www.screener.in/company/{symbol}/consolidated/"
MIN_INTERVAL = 3.0

RATIOS = re.compile(r'<li[^>]*class="flex flex-space-between[^"]*"[^>]*>(.*?)</li>', re.S)
TOP_BLOCK = re.compile(r'<ul[^>]*id="top-ratios".*?</ul>', re.S)
TAG = re.compile(r"<[^>]+>")
NUM = re.compile(r"-?[\d,]+(?:\.\d+)?")


class NotAllowed(FetchError):
    def __init__(self, message: str):
        super().__init__(message, retryable=False, status=403)


class Screener(Provider):
    source = "SCREENER"
    dataset = "company"
    tier = 3
    USER_AGENT = "Mozilla/5.0 (compatible; financial-brain/1.0; personal research)"

    def __init__(self, *, min_interval: float = MIN_INTERVAL):
        self.min_interval = min_interval
        self._robots: Robots | None = None
        self._last = 0.0

    def _rules(self) -> Robots | None:
        if self._robots is None:
            try:
                req = urllib.request.Request("https://www.screener.in/robots.txt",
                                             headers={"User-Agent": self.USER_AGENT})
                with urllib.request.urlopen(req, timeout=20) as r:
                    self._robots = Robots(r.read().decode("utf-8", "replace"),
                                          self.USER_AGENT)
            except (urllib.error.URLError, OSError):
                return None
        return self._robots

    def may_fetch(self, url: str) -> tuple[bool, str]:
        if urlparse(url).netloc.lower().removeprefix("www.") != "screener.in":
            return False, "not a screener.in URL"
        rules = self._rules()
        if rules is None:
            return False, "robots.txt could not be read"
        return (True, "allowed") if rules.can_fetch(url) else (False, "robots.txt disallows this path")

    def fetch(self, business_date) -> FetchResult:          # Provider interface
        raise NotImplementedError("screener is fetched per company, not per date")

    def fetch_company(self, symbol: str, *, consolidated: bool = False) -> FetchResult:
        url = (CONSOLIDATED if consolidated else BASE).format(symbol=symbol.upper())
        ok, why = self.may_fetch(url)
        if not ok:
            raise NotAllowed(f"refusing {url}: {why}")
        gap = time.monotonic() - self._last
        delay = max((self._rules().crawl_delay or 0), self.min_interval)
        if gap < delay:
            time.sleep(delay - gap)
        self._last = time.monotonic()
        payload, status, ctype = self._http_get(url, timeout=45, retries=2,
                                                headers={"User-Agent": self.USER_AGENT})
        return FetchResult(payload=payload, url=url, retrieved_at=self._now(),
                           filename=f"{symbol.upper()}.html", content_type=ctype,
                           http_status=status)


def _text(fragment: str) -> str:
    return " ".join(html.unescape(TAG.sub(" ", fragment)).split())


def _number(raw: str) -> tuple[float | None, str | None]:
    """The figure and its unit. "₹ 16,59,630 Cr." -> (16596300000000.0, 'INR'); Indian
    digit grouping means the commas cannot be assumed to be thousands separators, so
    they are simply removed."""
    m = NUM.search(raw)
    if not m:
        return None, None
    value = float(m.group(0).replace(",", ""))
    if "%" in raw:
        return value, "PCT"
    if "\u20b9" in raw or "Rs" in raw:
        if re.search(r"\bCr\.?", raw):
            value *= 1e7                      # 1 crore = 10,000,000
        return value, "INR"
    return value, None


def parse_company(payload: bytes) -> dict:
    """Company name and the headline ratio block, as numbers with units."""
    text = payload.decode("utf-8", errors="replace")
    name = None
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S)
    if h1:
        name = _text(h1.group(1)) or None
    block = TOP_BLOCK.search(text)
    ratios: dict[str, dict] = {}
    if block:
        for li in re.findall(r"<li[^>]*>(.*?)</li>", block.group(0), re.S):
            flat = _text(li)
            if not flat:
                continue
            label, _, rest = flat.partition("\u20b9") if "\u20b9" in flat else (None, None, None)
            if label is None:
                m = re.match(r"(.+?)\s+(-?[\d,.]+\s*%?)$", flat)
                if not m:
                    continue
                label, raw = m.group(1), m.group(2)
            else:
                raw = "\u20b9" + rest
            label = label.strip(" :")
            value, unit = _number(raw)
            parts = [p for p in label.split("/")]
            figures = NUM.findall(raw)
            if len(parts) == 2 and len(figures) == 2:
                # "High / Low  Rs 1,612 / 1,226" is two facts sharing one line.
                for sub, fig in zip(parts, figures):
                    v, u = _number(raw.split("/")[0].replace(figures[0], fig))
                    ratios[sub.strip()] = {"raw": fig, "value": v, "unit": u}
                continue
            ratios[label] = {"raw": raw.strip(), "value": value, "unit": unit}
    return {"name": name, "ratios": ratios}
