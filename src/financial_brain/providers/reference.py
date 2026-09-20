"""Reference and benchmark providers, all from NSE's public archive host.

Note on reachability: ``www.nseindia.com``'s JSON API rejects non-browser clients (HTTP
403), so the corporate-actions and ASM/GSM endpoints behind it are not usable from a
scheduled job. ``nsearchives.nseindia.com`` serves flat files without that gate, and
everything here comes from there.

The consequence is documented rather than worked around: surveillance flags (ASM/GSM/T2T)
have no archive-host source, so ``security_flags`` is populated only with **F&O
eligibility** - which is the flag that actually gates implementability, because it is the
binary "can this be shorted at all?" test for any long-short hypothesis.
"""
from __future__ import annotations

import csv
import io
from datetime import date

from .base import FetchError, FetchResult, NotPublished, Provider

NSE_REFERER = {"Referer": "https://www.nseindia.com/"}


class NSEIndexCloseProvider(Provider):
    """Daily close for every NSE index, plus P/E, P/B and dividend yield.

    One file per day covering ~165 indices. This is the benchmark history that
    docs/FEASIBILITY-INDIA.md flags as required before any strategy can be assessed
    honestly - you cannot claim outperformance without something to outperform.
    """

    source = "NSE"
    dataset = "index_close"
    tier = 1

    URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{ddmmyyyy}.csv"

    def fetch(self, business_date: date) -> FetchResult:
        url = self.URL.format(ddmmyyyy=business_date.strftime("%d%m%Y"))
        try:
            payload, status, ctype = self._http_get(url, headers=NSE_REFERER)
        except FetchError as e:
            if e.status == 404:
                raise NotPublished(f"NSE has no index close for {business_date}") from e
            raise
        if b"Index Name" not in payload[:200]:
            raise NotPublished(f"NSE index close for {business_date} is not a CSV")
        return FetchResult(payload=payload, url=url, retrieved_at=self._now(),
                           filename=f"ind_close_all_{business_date:%Y%m%d}.csv",
                           content_type=ctype, http_status=status)

    @staticmethod
    def parse(payload: bytes, business_date: date) -> list[dict]:
        rows = []
        for r in csv.DictReader(io.StringIO(payload.decode("utf-8", errors="replace"))):
            name = (r.get("Index Name") or "").strip()
            if not name:
                continue

            def num(key, r=r):          # bind this row; the name is reused each loop
                v = (r.get(key) or "").strip().replace(",", "")
                try:
                    return float(v)
                except ValueError:
                    return None

            rows.append({
                "business_date": business_date,
                "index_name": name,
                "open_level": num("Open Index Value"),
                "high_level": num("High Index Value"),
                "low_level": num("Low Index Value"),
                "close_level": num("Closing Index Value"),
                "pe": num("P/E"), "pb": num("P/B"), "div_yield": num("Div Yield"),
            })
        return rows


class NSEEquityListProvider(Provider):
    """NSE's master list of listed equities: ISIN, series, listing date, face value.

    Not dated - it is a current snapshot, which is exactly why it is ingested with an
    ``observed_at`` and never overwritten. Listing date is the valuable column: it bounds
    how far back a name could possibly have belonged to any universe.
    """

    source = "NSE"
    dataset = "equity_list"
    tier = 1

    URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

    def fetch(self, business_date: date) -> FetchResult:
        payload, status, ctype = self._http_get(self.URL, headers=NSE_REFERER)
        if b"SYMBOL" not in payload[:100]:
            raise FetchError("EQUITY_L.csv did not look like the master list", retryable=False)
        return FetchResult(payload=payload, url=self.URL, retrieved_at=self._now(),
                           filename=f"EQUITY_L_{business_date:%Y%m%d}.csv",
                           content_type=ctype, http_status=status)

    @staticmethod
    def parse(payload: bytes) -> list[dict]:
        rows = []
        for r in csv.DictReader(io.StringIO(payload.decode("utf-8", errors="replace"))):
            clean = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
            isin = clean.get("ISIN NUMBER", "")
            if not isin:
                continue

            def num(key, clean=clean):  # bind this row; the name is reused each loop
                try:
                    return float(clean.get(key, "") or 0) or None
                except ValueError:
                    return None

            listing = None
            raw = clean.get("DATE OF LISTING", "")
            if raw:
                from datetime import datetime
                # NSE writes "25-AUG-2004". %b is case-insensitive in strptime; the
                # format string itself must not be uppercased (%d -> %D breaks it).
                for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%Y-%m-%d"):
                    try:
                        listing = datetime.strptime(raw, fmt).date()
                        break
                    except ValueError:
                        continue

            rows.append({
                "isin": isin, "exchange": "NSE", "symbol": clean.get("SYMBOL", ""),
                "company_name": clean.get("NAME OF COMPANY", ""),
                "series": clean.get("SERIES", ""), "listing_date": listing,
                "paid_up_value": num("PAID UP VALUE"), "face_value": num("FACE VALUE"),
                "market_lot": int(num("MARKET LOT") or 1),
            })
        return rows


class NSEFnoLotsProvider(Provider):
    """F&O underlyings and their lot sizes.

    Its real job here is the **F&O eligibility flag**. Indian cash equities cannot be
    shorted beyond intraday and SLB is thin, so for any long-short hypothesis the binary
    question is "is there a future on this?" - and this file is the answer.
    """

    source = "NSE"
    dataset = "fo_lots"
    tier = 1

    URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"

    def fetch(self, business_date: date) -> FetchResult:
        payload, status, ctype = self._http_get(self.URL, headers=NSE_REFERER)
        if b"UNDERLYING" not in payload[:200]:
            raise FetchError("fo_mktlots.csv did not look like the lot-size file",
                             retryable=False)
        return FetchResult(payload=payload, url=self.URL, retrieved_at=self._now(),
                           filename=f"fo_mktlots_{business_date:%Y%m%d}.csv",
                           content_type=ctype, http_status=status)

    @staticmethod
    def parse(payload: bytes) -> list[dict]:
        text = payload.decode("utf-8", errors="replace")
        rows = []
        reader = csv.reader(io.StringIO(text))
        header = next(reader, None)
        if not header:
            return rows
        for r in reader:
            if len(r) < 3:
                continue
            underlying, symbol = r[0].strip(), r[1].strip()
            if not symbol or symbol.upper() == "SYMBOL":
                continue
            lot = None
            for cell in r[2:]:
                cell = cell.strip()
                if cell.isdigit():
                    lot = int(cell)
                    break
            rows.append({"underlying": underlying, "symbol": symbol, "lot_size": lot})
        return rows


REFERENCE_PROVIDERS = {
    "index_close": NSEIndexCloseProvider,
    "equity_list": NSEEquityListProvider,
    "fo_lots": NSEFnoLotsProvider,
}
