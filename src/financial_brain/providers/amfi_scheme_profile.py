"""Parse source-attributed AMC/SID mutual-fund scheme profile disclosures."""
from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date, datetime
from urllib.parse import urlparse

from .base import FetchError

_HASH = re.compile(r"^[0-9a-fA-F]{64}$")
_BENCHMARK_TYPES = {"TRI", "PRICE", "OTHER", "UNKNOWN"}


def parse(payload: bytes, observed_on: date) -> list[dict]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FetchError("scheme profile CSV must be UTF-8", retryable=False) from exc
    reader = csv.DictReader(io.StringIO(text))
    required = {"scheme_code", "source_url", "source_document_sha256", "source_published_at"}
    headers = {str(x or "").strip().lower() for x in (reader.fieldnames or [])}
    missing = required - headers
    if missing:
        raise FetchError(f"scheme profile CSV missing columns: {', '.join(sorted(missing))}",
                         retryable=False)
    rows: list[dict] = []
    seen: set[str] = set()
    for raw in reader:
        row = {(key or "").strip().lower(): (value or "").strip()
               for key, value in raw.items()}
        code = row.get("scheme_code", "")
        identity_values = (code, row.get("plan", ""), row.get("option", ""),
                           row.get("isin", "").upper())
        if not code or any(len(value) > 240 for value in identity_values):
            raise FetchError("scheme code/identity is empty or too long", retryable=False)
        identity_key = hashlib.sha256("|".join(identity_values).encode()).hexdigest()
        if identity_key in seen:
            raise FetchError(f"duplicate scheme profile identity: {code}", retryable=False)
        seen.add(identity_key)
        url = row.get("source_url", "")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise FetchError(f"scheme {code} source_url must be HTTPS", retryable=False)
        doc_hash = row.get("source_document_sha256", "").lower()
        if not _HASH.fullmatch(doc_hash):
            raise FetchError(f"scheme {code} source document SHA-256 is invalid", retryable=False)
        try:
            published = datetime.fromisoformat(row["source_published_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise FetchError(f"scheme {code} has invalid source_published_at", retryable=False) from exc
        if published.tzinfo is None or published.date() > observed_on:
            raise FetchError(f"scheme {code} publication time is invalid for observation date",
                             retryable=False)
        facts = {key: row.get(key) or None for key in
                 ("riskometer", "benchmark_name", "manager", "objective")}
        if not any(facts.values()):
            raise FetchError(f"scheme {code} row contains no profile facts", retryable=False)
        raw_benchmark_type = row.get("benchmark_return_type", "").strip()
        benchmark_type = (raw_benchmark_type or "UNKNOWN").upper()
        if benchmark_type not in _BENCHMARK_TYPES:
            raise FetchError(f"scheme {code} has invalid benchmark_return_type", retryable=False)
        if facts["benchmark_name"] is None and benchmark_type != "UNKNOWN":
            raise FetchError(f"scheme {code} benchmark type requires a benchmark name",
                             retryable=False)
        if facts["benchmark_name"] is not None and not raw_benchmark_type:
            raise FetchError(f"scheme {code} benchmark requires an explicit return type",
                             retryable=False)
        dates = {}
        for key in ("effective_from", "effective_to"):
            try:
                dates[key] = date.fromisoformat(row[key]) if row.get(key) else None
            except ValueError as exc:
                raise FetchError(f"scheme {code} has invalid {key}", retryable=False) from exc
        if dates["effective_from"] and dates["effective_to"] and dates["effective_to"] < dates["effective_from"]:
            raise FetchError(f"scheme {code} effective dates are reversed", retryable=False)
        rows.append({"identity_key": identity_key, "scheme_code": code,
                     "plan": identity_values[1] or None, "option": identity_values[2] or None,
                     "isin": identity_values[3] or None, **facts,
                     "benchmark_return_type": benchmark_type,
                     **dates, "source_url": url, "source_document_sha256": doc_hash,
                     "source_published_at": published})
    if not rows:
        raise FetchError("scheme profile CSV contains no rows", retryable=False)
    return rows
