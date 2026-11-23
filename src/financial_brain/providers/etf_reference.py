"""NSE's published ETF securities list as an immutable reference snapshot.

The current list identifies NSE-traded ETF symbols, underlying labels, listing dates,
market lots and ISINs. It is a snapshot, not a complete historical listing-status feed;
consumers must retain each observed file and must not infer delisting from omission.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .base import FetchError, FetchResult, Provider

NSE_ETF_LIST_URL = "https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv"
NSE_REFERER = {"Referer": "https://www.nseindia.com/"}


def _header_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").replace("\ufeff", "").lower())


def _listing_date(value: str) -> date | None:
    value = value.strip()
    if not value or value in {"-", "NA", "N/A"}:
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise FetchError(f"unrecognized ETF listing date {value!r}", retryable=False)


def parse(payload: bytes, observed_on: date) -> list[dict]:
    """Normalize the NSE ETF reference CSV; reject incomplete or duplicate identities."""
    decoded = None
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            decoded = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            pass
    if decoded is None:
        raise FetchError("ETF reference file has an unsupported encoding", retryable=False)

    reader = csv.DictReader(io.StringIO(decoded))
    fields = {_header_key(header): header for header in (reader.fieldnames or [])}
    aliases = {
        "symbol": ("symbol",),
        "underlying": ("underlying", "underlyingasset", "underlyingindex"),
        "security_name": ("securityname", "nameofsecurity"),
        "listing_date": ("dateoflisting", "listingdate"),
        "market_lot": ("marketlot", "lot"),
        "isin": ("isinnumber", "isin"),
        "face_value": ("facevalue",),
    }
    columns: dict[str, str | None] = {}
    for target, options in aliases.items():
        columns[target] = next((fields[key] for key in options if key in fields), None)
    missing = [name for name in ("symbol", "security_name", "isin") if columns[name] is None]
    if missing:
        raise FetchError(f"ETF reference file missing required columns: {', '.join(missing)}",
                         retryable=False)

    rows: list[dict] = []
    seen_isin: set[str] = set()
    seen_symbol: set[str] = set()
    for raw in reader:
        clean = {key: (raw.get(column) or "").strip() if column else ""
                 for key, column in columns.items()}
        if not any(clean.values()):
            continue
        symbol, isin = clean["symbol"].upper(), clean["isin"].upper()
        if not symbol or len(isin) != 12 or not isin.isalnum() or not clean["security_name"]:
            raise FetchError("ETF row missing symbol, valid 12-character ISIN or name",
                             retryable=False)
        if symbol in seen_symbol or isin in seen_isin:
            raise FetchError(f"duplicate ETF identity in reference file: {symbol}/{isin}",
                             retryable=False)
        seen_symbol.add(symbol)
        seen_isin.add(isin)
        listing_date = _listing_date(clean["listing_date"])
        if listing_date and listing_date > observed_on:
            raise FetchError(f"ETF {symbol} listing date is after the snapshot date",
                             retryable=False)
        try:
            lot = int(clean["market_lot"]) if clean["market_lot"] else 1
            face_value = Decimal(clean["face_value"].replace(",", "")) if clean["face_value"] else None
        except (ValueError, InvalidOperation) as exc:
            raise FetchError(f"ETF {symbol} has invalid lot or face value", retryable=False) from exc
        if lot < 1 or (face_value is not None and face_value <= 0):
            raise FetchError(f"ETF {symbol} has non-positive lot or face value", retryable=False)
        rows.append({
            "observed_on": observed_on,
            "exchange": "NSE",
            "symbol": symbol,
            "underlying": clean["underlying"] or None,
            "security_name": clean["security_name"],
            "listing_date": listing_date,
            "market_lot": lot,
            "isin": isin,
            "face_value": face_value,
            "status": "LISTED",
        })
    if not rows:
        raise FetchError("ETF reference file contains no valid ETF rows", retryable=False)
    return rows


class NSEETFReferenceProvider(Provider):
    """Fetch the exchange's current ETF securities list (read-only reference data)."""

    source = "NSE"
    dataset = "etf_reference"
    tier = 1

    def fetch(self, business_date: date) -> FetchResult:
        payload, status, content_type = self._http_get(
            NSE_ETF_LIST_URL, headers=NSE_REFERER, timeout=60
        )
        # Validate before the bytes enter the immutable lake; malformed downloads fail loudly.
        parse(payload, business_date)
        return FetchResult(
            payload=payload,
            url=NSE_ETF_LIST_URL,
            retrieved_at=self._now(),
            filename=f"eq_etfseclist_{business_date:%Y%m%d}.csv",
            content_type=content_type,
            http_status=status,
        )
