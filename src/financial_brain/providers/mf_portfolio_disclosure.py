"""Parse normalized, source-attributed AMC portfolio disclosure CSV rows."""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from .base import FetchError

_HASH = re.compile(r"^[0-9a-f]{64}$")


def parse(payload: bytes, observed_on: date) -> list[dict]:
    try:
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    except UnicodeDecodeError as exc:
        raise FetchError("portfolio disclosure CSV must be UTF-8", retryable=False) from exc
    required = {"scheme_code", "portfolio_as_of", "source_url", "source_document_sha256",
                "source_published_at"}
    headers = {str(x or "").strip().lower() for x in (reader.fieldnames or [])}
    if not required <= headers:
        raise FetchError(f"portfolio CSV missing columns: {', '.join(sorted(required - headers))}",
                         retryable=False)
    out, seen = [], set()
    for raw in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        try:
            as_of = date.fromisoformat(row["portfolio_as_of"])
            published = datetime.fromisoformat(row["source_published_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise FetchError("portfolio dates must be ISO dates/timestamps", retryable=False) from exc
        if not row["scheme_code"] or as_of > observed_on or published.tzinfo is None or published.date() > observed_on:
            raise FetchError("invalid scheme identity, future portfolio date or publication timestamp",
                             retryable=False)
        url = row["source_url"]
        if urlparse(url).scheme != "https" or not urlparse(url).netloc:
            raise FetchError("source_url must be HTTPS", retryable=False)
        digest = row["source_document_sha256"].lower()
        if not _HASH.fullmatch(digest):
            raise FetchError("source_document_sha256 must be a SHA-256 digest", retryable=False)
        underlying = (row.get("underlying_scheme_code") or "").strip()
        instrument = (row.get("instrument_id") or row.get("isin") or row.get("holding_name")
                      or (f"MF:{underlying}" if underlying else "")).upper()
        if not instrument:
            raise FetchError("each row requires instrument_id, ISIN or holding_name", retryable=False)
        key = (row["scheme_code"], as_of, instrument)
        if key in seen:
            raise FetchError(f"duplicate holding row: {key}", retryable=False)
        seen.add(key)
        nums = {}
        for field in ("weight_pct", "market_value_inr", "quantity", "modified_duration_years",
                      "yield_to_maturity_pct"):
            try:
                nums[field] = Decimal(row[field]) if row.get(field) else None
            except InvalidOperation as exc:
                raise FetchError(f"invalid {field}", retryable=False) from exc
            if nums[field] is not None and (not nums[field].is_finite() or nums[field] < 0):
                raise FetchError(f"{field} must be finite and non-negative", retryable=False)
        if nums["weight_pct"] is not None and nums["weight_pct"] > 100:
            raise FetchError("weight_pct cannot exceed 100", retryable=False)
        out.append({"scheme_code": row["scheme_code"], "portfolio_as_of": as_of,
                    "instrument_id": instrument, "underlying_scheme_code": underlying or None,
                    "holding_name": row.get("holding_name") or None,
                    "asset_type": row.get("asset_type") or "UNKNOWN",
                    "sector": row.get("sector") or None, "credit_rating": row.get("credit_rating") or None,
                    "maturity_date": date.fromisoformat(row["maturity_date"]) if row.get("maturity_date") else None,
                    **nums, "observed_on": observed_on, "source_url": url,
                    "source_document_sha256": digest, "source_published_at": published})
    if not out:
        raise FetchError("portfolio disclosure CSV contains no holdings", retryable=False)
    return out
