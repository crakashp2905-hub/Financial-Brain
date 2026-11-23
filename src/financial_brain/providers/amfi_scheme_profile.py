"""Parse source-attributed AMC/SID mutual-fund scheme profile disclosures."""
from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from .base import FetchError

_HASH = re.compile(r"^[0-9a-fA-F]{64}$")
_BENCHMARK_TYPES = {"TRI", "PRICE", "OTHER", "UNKNOWN"}
_SCHEME_STATUSES = {"ACTIVE", "CLOSED_FOR_SUBSCRIPTION", "CLOSED", "WOUND_UP",
                    "MERGED", "RENAMED", "SUSPENDED", "UNKNOWN"}


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
                 ("riskometer", "benchmark_name", "manager", "objective",
                  "exit_load_schedule", "exit_restrictions")}
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
        for key in ("effective_from", "effective_to", "status_effective_from",
                    "expense_effective_from", "expense_effective_to", "aum_as_of",
                    "minimum_effective_from", "minimum_effective_to",
                    "exit_load_effective_from", "exit_load_effective_to",
                    "restriction_effective_from", "restriction_effective_to"):
            try:
                dates[key] = date.fromisoformat(row[key]) if row.get(key) else None
            except ValueError as exc:
                raise FetchError(f"scheme {code} has invalid {key}", retryable=False) from exc
        if dates["effective_from"] and dates["effective_to"] and dates["effective_to"] < dates["effective_from"]:
            raise FetchError(f"scheme {code} effective dates are reversed", retryable=False)
        status = (row.get("scheme_status") or "").upper() or None
        if status is not None and status not in _SCHEME_STATUSES:
            raise FetchError(f"scheme {code} has invalid scheme_status", retryable=False)
        successor = row.get("successor_scheme_code") or None
        if status is not None and dates["status_effective_from"] is None:
            raise FetchError(f"scheme {code} status requires status_effective_from",
                             retryable=False)
        if status == "MERGED" and successor is None:
            raise FetchError(f"scheme {code} merged status requires successor_scheme_code",
                             retryable=False)
        amount_fields: dict[str, Decimal | None] = {}
        for key in ("expense_ratio_pct", "aum_inr", "minimum_initial_inr",
                    "minimum_additional_inr"):
            try:
                amount_fields[key] = Decimal(row[key]) if row.get(key) else None
            except InvalidOperation as exc:
                raise FetchError(f"scheme {code} has invalid {key}", retryable=False) from exc
            value = amount_fields[key]
            if value is not None and (not value.is_finite() or value < 0):
                raise FetchError(f"scheme {code} {key} must be finite and non-negative",
                                 retryable=False)
        if amount_fields["expense_ratio_pct"] is not None:
            if amount_fields["expense_ratio_pct"] >= 100:
                raise FetchError(f"scheme {code} expense ratio must be below 100 percent",
                                 retryable=False)
            plan = (row.get("expense_ratio_plan") or "").upper()
            if plan not in {"DIRECT", "REGULAR"} or dates["expense_effective_from"] is None:
                raise FetchError(f"scheme {code} TER requires DIRECT/REGULAR plan and effective date",
                                 retryable=False)
        else:
            plan = None
        if amount_fields["aum_inr"] is not None and dates["aum_as_of"] is None:
            raise FetchError(f"scheme {code} AUM requires aum_as_of", retryable=False)
        if amount_fields["aum_inr"] == 0:
            raise FetchError(f"scheme {code} AUM must be positive", retryable=False)
        if dates["aum_as_of"] and dates["aum_as_of"] > observed_on:
            raise FetchError(f"scheme {code} AUM date is after observation date", retryable=False)
        if any(amount_fields[key] is not None for key in
               ("minimum_initial_inr", "minimum_additional_inr")) \
                and dates["minimum_effective_from"] is None:
            raise FetchError(f"scheme {code} minimum investments require an effective date",
                             retryable=False)
        if facts["exit_load_schedule"] and dates["exit_load_effective_from"] is None:
            raise FetchError(f"scheme {code} exit-load schedule requires an effective date",
                             retryable=False)
        if facts["exit_restrictions"] and dates["restriction_effective_from"] is None:
            raise FetchError(f"scheme {code} exit restrictions require an effective date",
                             retryable=False)
        for start_key, end_key in (("expense_effective_from", "expense_effective_to"),
                                   ("minimum_effective_from", "minimum_effective_to"),
                                   ("exit_load_effective_from", "exit_load_effective_to"),
                                   ("restriction_effective_from", "restriction_effective_to")):
            if dates[start_key] and dates[end_key] and dates[end_key] < dates[start_key]:
                raise FetchError(f"scheme {code} {start_key} window is reversed", retryable=False)
        if (not any(facts.values()) and status is None
                and not any(value is not None for value in amount_fields.values())):
            raise FetchError(f"scheme {code} row contains no profile/economic facts", retryable=False)
        rows.append({"identity_key": identity_key, "scheme_code": code,
                     "plan": identity_values[1] or None, "option": identity_values[2] or None,
                     "isin": identity_values[3] or None, **facts,
                     "benchmark_return_type": benchmark_type,
                     **dates, "scheme_status": status, "successor_scheme_code": successor,
                     "status_reason": row.get("status_reason") or None,
                     **amount_fields, "expense_ratio_plan": plan,
                     "source_url": url, "source_document_sha256": doc_hash,
                     "source_published_at": published})
    if not rows:
        raise FetchError("scheme profile CSV contains no rows", retryable=False)
    return rows
