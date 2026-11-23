"""Parse source-attributed ETF product facts supplied from official disclosures.

This adapter intentionally does not infer benchmark variant, AMC, inception, NAV or iNAV
from a ticker/name. Each populated row carries the official document URL, its SHA-256 and
publication time so human-transcribed facts remain auditable.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from .base import FetchError

_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
_RETURN_TYPES = {"TRI", "PRICE", "OTHER", "UNKNOWN"}


def parse(payload: bytes, observed_on: date) -> list[dict]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FetchError("ETF disclosure CSV must be UTF-8", retryable=False) from exc
    reader = csv.DictReader(io.StringIO(text))
    required = {"isin", "source_url", "source_document_sha256", "source_published_at"}
    headers = {name.strip().lower() for name in (reader.fieldnames or [])}
    missing = required - headers
    if missing:
        raise FetchError(f"ETF disclosure CSV missing columns: {', '.join(sorted(missing))}",
                         retryable=False)

    result: list[dict] = []
    seen: set[str] = set()
    for raw in reader:
        row = {(key or "").strip().lower(): (value or "").strip()
               for key, value in raw.items()}
        isin = row.get("isin", "").upper()
        if len(isin) != 12 or not isin.isalnum() or isin in seen:
            raise FetchError(f"invalid or duplicate ETF disclosure ISIN: {isin!r}",
                             retryable=False)
        seen.add(isin)
        url = row.get("source_url", "")
        parsed_url = urlparse(url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise FetchError(f"ETF {isin} source_url must be an HTTPS URL", retryable=False)
        document_hash = row.get("source_document_sha256", "").lower()
        if not _HEX.fullmatch(document_hash):
            raise FetchError(f"ETF {isin} source document SHA-256 is invalid", retryable=False)
        try:
            published_at = datetime.fromisoformat(row["source_published_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise FetchError(f"ETF {isin} has invalid source_published_at", retryable=False) from exc
        if published_at.tzinfo is None or published_at.date() > observed_on:
            raise FetchError(f"ETF {isin} source publication time is invalid for snapshot date",
                             retryable=False)

        benchmark = row.get("benchmark_name") or None
        return_type = (row.get("benchmark_return_type") or "UNKNOWN").upper()
        if return_type not in _RETURN_TYPES:
            raise FetchError(f"ETF {isin} benchmark_return_type must be one of "
                             f"{', '.join(sorted(_RETURN_TYPES))}", retryable=False)
        if benchmark is None and return_type != "UNKNOWN":
            raise FetchError(f"ETF {isin} cannot declare benchmark return type without benchmark",
                             retryable=False)

        fields: dict[str, date | Decimal | None] = {}
        for name in ("inception_date", "listing_date", "nav_as_of"):
            value = row.get(name, "")
            try:
                fields[name] = date.fromisoformat(value) if value else None
            except ValueError as exc:
                raise FetchError(f"ETF {isin} has invalid {name}", retryable=False) from exc
        for name in ("nav", "indicative_nav"):
            value = row.get(name, "")
            try:
                fields[name] = Decimal(value) if value else None
            except InvalidOperation as exc:
                raise FetchError(f"ETF {isin} has invalid {name}", retryable=False) from exc
            if fields[name] is not None and fields[name] <= 0:
                raise FetchError(f"ETF {isin} {name} must be positive", retryable=False)
        inav_time = row.get("indicative_nav_at", "")
        try:
            indicative_nav_at = (datetime.fromisoformat(inav_time.replace("Z", "+00:00"))
                                 if inav_time else None)
        except ValueError as exc:
            raise FetchError(f"ETF {isin} has invalid indicative_nav_at", retryable=False) from exc
        if indicative_nav_at and indicative_nav_at.tzinfo is None:
            raise FetchError(f"ETF {isin} indicative_nav_at must include a timezone",
                             retryable=False)
        if (fields["indicative_nav"] is None) != (indicative_nav_at is None):
            raise FetchError(f"ETF {isin} iNAV value and timestamp must be supplied together",
                             retryable=False)
        if any(d is not None and d > observed_on for d in
               (fields["inception_date"], fields["listing_date"], fields["nav_as_of"])):
            raise FetchError(f"ETF {isin} has a fact dated after the snapshot date",
                             retryable=False)
        result.append({
            "isin": isin,
            "benchmark_name": benchmark,
            "benchmark_return_type": return_type,
            "fund_house": row.get("fund_house") or None,
            "inception_date": fields["inception_date"],
            "listing_date": fields["listing_date"],
            "nav": fields["nav"],
            "nav_as_of": fields["nav_as_of"],
            "indicative_nav": fields["indicative_nav"],
            "indicative_nav_at": indicative_nav_at,
            "source_url": url,
            "source_document_sha256": document_hash,
            "source_published_at": published_at,
        })
    if not result:
        raise FetchError("ETF disclosure CSV contains no records", retryable=False)
    return result


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
