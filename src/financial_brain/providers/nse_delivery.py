"""NSE security-wise delivery position (the "MTO" file) - how much of each day's traded quantity went to delivery.

One file per trading session on NSE's archive host, the same host the bhavcopy comes from:

    https://nsearchives.nseindia.com/archives/equities/mto/MTO_DDMMYYYY.DAT

    Security Wise Delivery Position - Compulsory Rolling Settlement
    10,MTO,04012016,450374569,0001561
    Trade Date <04-JAN-2016>,Settlement Type <N>,...
    Record Type,Sr No,Name of Security,Quantity Traded,Deliverable Quantity(...),% of Deliverable Quantity to Traded Quantity
    20,1,20MICRONS,EQ,258669,116118,44.89

Record type 20 is a security row (symbol, series, traded quantity, deliverable quantity, delivery %). The trade date
in the header is what the rows are filed under - never the date asked for. Available from 2015 (checked 2026-10-13).
A 404 is a non-session day.

Tier 1: the exchange's own settlement statistics.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from .base import FetchError, FetchResult, NotPublished, Provider

URL = "https://nsearchives.nseindia.com/archives/equities/mto/MTO_{d:%d%m%Y}.DAT"
TRADE_DATE = re.compile(r"Trade Date <(\d{2}-[A-Z]{3}-\d{4})>")


class NseDelivery(Provider):
    source = "NSE"
    dataset = "delivery_mto"
    tier = 1

    def fetch(self, business_date: date) -> FetchResult:
        url = URL.format(d=business_date)
        try:
            payload, status, ctype = self._http_get(url, timeout=45)
        except FetchError as e:
            if e.status == 404:
                raise NotPublished(f"no MTO file for {business_date}") from e
            raise
        if b"Security Wise Delivery Position" not in payload[:200]:
            raise FetchError(f"MTO file for {business_date} has an unexpected header: {payload[:80]!r}",
                             retryable=True, status=status)
        return FetchResult(payload=payload, url=url, retrieved_at=self._now(),
                           filename=f"MTO_{business_date:%d%m%Y}.DAT", content_type=ctype, http_status=status)


def parse(payload: bytes) -> tuple[date | None, list[dict]]:
    """(trade date from the header, rows). Rows with a non-numeric quantity are dropped, not zero-filled."""
    text = payload.decode("latin-1")
    m = TRADE_DATE.search(text[:400])
    trade = datetime.strptime(m.group(1).title(), "%d-%b-%Y").date() if m else None
    rows = []
    for line in text.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 7 or f[0] != "20":
            continue
        try:
            traded, deliv, pct = int(f[4]), int(f[5]), float(f[6])
        except ValueError:
            continue
        rows.append({"symbol": f[2], "series": f[3], "qty_traded": traded, "deliv_qty": deliv, "deliv_pct": pct})
    return trade, rows
