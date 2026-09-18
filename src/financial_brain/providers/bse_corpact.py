"""BSE corporate actions - the authoritative feed Phase 0 lacked (Tier 1).

``api.bseindia.com/BseIndiaAPI/api/DefaultData/w`` returns every corporate action in a
date window: scrip code, ex-date, record date and a free-text *purpose*. It needs no
browser session (unlike NSE's equivalent behind ``www.nseindia.com``), so it is fetched
like every other source: lake first, then parsed.

Purposes, surveyed over 2015-2026, are regular enough for a strict parser:

    "Stock  Split From Rs.10/- to Rs.2/-"   -> SPLIT, factor = new FV / old FV = 0.2
    "Bonus issue 1:1"                       -> BONUS, a new for every b held,
                                               factor = b / (a + b) = 0.5
    "Final Dividend - Rs. - 2.5000"         -> DIVIDEND, amount 2.5 (no price factor)
    "Right Issue of Equity Shares", "Buy Back of Shares", "Spin Off",
    "Reduction of Capital", "Amalgamation", "Scheme of Arrangement", ...
                                            -> typed, no factor inferred

**Strict by design:** a purpose the parser does not fully understand is kept with its
type and raw text but no factor. A wrong factor silently corrupts adjusted history; a
missing one is visible.
"""
from __future__ import annotations

import json
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from fractions import Fraction

from .base import FetchError, FetchResult, Provider

URL = ("https://api.bseindia.com/BseIndiaAPI/api/DefaultData/w?Fdate={f}&Purposecode="
       "&TDate={t}&ddlcategorys=E&ddlindustrys=&scripcode=&segment=0&strSearch=S")
HEADERS = {"Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com",
           "Accept": "application/json, text/plain, */*"}


@dataclass
class ParsedAction:
    scrip_code: str
    short_name: str
    ex_date: date | None
    record_date: date | None
    purpose: str
    action_type: str
    ratio_from: float | None = None      # price factor = ratio_from / ratio_to
    ratio_to: float | None = None
    amount: float | None = None


_SPLIT = re.compile(r"split\s+from\s+rs\.?\s*([\d.]+)\s*/?-?\s*to\s+rs\.?\s*([\d.]+)", re.I)
_BONUS = re.compile(r"bonus\s+issue\s+(\d+)\s*:\s*(\d+)", re.I)
_DIVIDEND = re.compile(r"dividend\s*-\s*rs\.?\s*-\s*([\d.]+)", re.I)

_TYPES = [  # (pattern, type) - checked in order after split/bonus/dividend
    (r"right\s+issue", "RIGHTS"), (r"buy\s*back", "BUYBACK"), (r"spin\s*off", "SPIN_OFF"),
    (r"reduction\s+of\s+capital", "CAPITAL_REDUCTION"), (r"amalgamation", "MERGER"),
    (r"scheme\s+of\s+arrangement", "SCHEME"), (r"consolidation", "CONSOLIDATION"),
    (r"suspension", "SUSPENSION"), (r"income\s+distribution", "DISTRIBUTION"),
    (r"return\s+of\s+capital", "RETURN_OF_CAPITAL"), (r"e\.?g\.?m", "MEETING"),
    (r"e-voting", "MEETING"),
]


def _d(s: str) -> date | None:
    s = (s or "").strip()
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_purpose(purpose: str) -> tuple[str, float | None, float | None, float | None]:
    """Return ``(action_type, ratio_from, ratio_to, amount)`` for a BSE purpose string."""
    p = " ".join((purpose or "").split())
    if m := _SPLIT.search(p):
        old, new = Fraction(m.group(1)), Fraction(m.group(2))
        if old > 0 and new > 0 and new < old:
            f = new / old                                   # e.g. 2/10 -> 1/5
            return "SPLIT", float(f.numerator), float(f.denominator), None
        return "SPLIT", None, None, None                    # odd FV change: no factor
    if m := _BONUS.search(p):
        a, b = int(m.group(1)), int(m.group(2))
        if a > 0 and b > 0:
            f = Fraction(b, a + b)                          # a new for every b held
            return "BONUS", float(f.numerator), float(f.denominator), None
        return "BONUS", None, None, None
    if m := _DIVIDEND.search(p):
        return "DIVIDEND", None, None, float(m.group(1))
    for pat, kind in _TYPES:
        if re.search(pat, p, re.I):
            return kind, None, None, None
    return "OTHER", None, None, None


class BSECorporateActionsProvider(Provider):
    source = "BSE"
    dataset = "corporate_actions"
    tier = 1

    def fetch_month(self, year: int, month: int) -> FetchResult:
        first, last = date(year, month, 1), date(year, month, monthrange(year, month)[1])
        url = URL.format(f=first.strftime("%Y%m%d"), t=last.strftime("%Y%m%d"))
        payload, status, ctype = self._http_get(url, headers=HEADERS)
        if not payload.lstrip().startswith(b"["):
            raise FetchError(f"BSE corporate actions {year}-{month:02d}: not a JSON list",
                             retryable=True)
        return FetchResult(payload=payload, url=url, retrieved_at=self._now(),
                           filename=f"bse_corpact_{year}{month:02d}.json",
                           content_type=ctype, http_status=status)

    def fetch(self, business_date: date) -> FetchResult:  # pragma: no cover
        return self.fetch_month(business_date.year, business_date.month)

    @staticmethod
    def parse(payload: bytes) -> list[ParsedAction]:
        out = []
        for r in json.loads(payload):
            purpose = (r.get("Purpose") or "").strip()
            kind, rf, rt, amt = parse_purpose(purpose)
            out.append(ParsedAction(
                scrip_code=str(r.get("scrip_code") or "").strip(),
                short_name=(r.get("short_name") or "").strip(),
                ex_date=_d(r.get("Ex_date") or r.get("exdate") or ""),
                record_date=_d(r.get("RD_Date") or ""),
                purpose=purpose, action_type=kind, ratio_from=rf, ratio_to=rt, amount=amt))
        return out
