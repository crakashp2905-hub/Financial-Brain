"""Parse source-attributed ETF product facts supplied from official disclosures.

This adapter intentionally does not infer benchmark variant, AMC, inception, NAV or iNAV
from a ticker/name. Each populated row carries the official document URL, its SHA-256 and
publication time so human-transcribed facts remain auditable. Creation/redemption figures
are issuer/market-maker primary-market context only; they do not describe a retail holder's
redemption entitlement or create a broker order path.
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
        for name in ("inception_date", "listing_date", "nav_as_of", "expense_effective_from",
                     "expense_effective_to", "tracking_period_start", "tracking_period_end"):
            value = row.get(name, "")
            try:
                fields[name] = date.fromisoformat(value) if value else None
            except ValueError as exc:
                raise FetchError(f"ETF {isin} has invalid {name}", retryable=False) from exc
        for name in ("nav", "indicative_nav", "expense_ratio_pct", "tracking_error_pct",
                     "tracking_difference_pct", "creation_units", "redemption_units",
                     "creation_unit_size"):
            value = row.get(name, "")
            try:
                fields[name] = Decimal(value) if value else None
            except InvalidOperation as exc:
                raise FetchError(f"ETF {isin} has invalid {name}", retryable=False) from exc
            if fields[name] is not None and name in {"nav", "indicative_nav"} \
                    and fields[name] <= 0:
                raise FetchError(f"ETF {isin} {name} must be positive", retryable=False)
            if fields[name] is not None and name in {
                    "expense_ratio_pct", "tracking_error_pct", "tracking_difference_pct"} \
                    and abs(fields[name]) >= 100:
                raise FetchError(f"ETF {isin} {name} must be below 100 percent in magnitude",
                                 retryable=False)
        if fields["expense_ratio_pct"] is not None and fields["expense_ratio_pct"] < 0:
            raise FetchError(f"ETF {isin} expense ratio cannot be negative", retryable=False)
        if fields["tracking_error_pct"] is not None and fields["tracking_error_pct"] < 0:
            raise FetchError(f"ETF {isin} tracking error cannot be negative", retryable=False)
        if fields["expense_ratio_pct"] is not None and fields["expense_effective_from"] is None:
            raise FetchError(f"ETF {isin} expense ratio requires an effective-from date",
                             retryable=False)
        if (fields["expense_ratio_pct"] is None
                and (fields["expense_effective_from"] or fields["expense_effective_to"])):
            raise FetchError(f"ETF {isin} cannot have expense dates without an expense ratio",
                             retryable=False)
        if (fields["expense_effective_from"] and fields["expense_effective_to"]
                and fields["expense_effective_to"] < fields["expense_effective_from"]):
            raise FetchError(f"ETF {isin} expense effective dates are reversed", retryable=False)
        has_tracking = any(fields[name] is not None for name in
                           ("tracking_error_pct", "tracking_difference_pct"))
        has_tracking_window = (fields["tracking_period_start"] is not None
                               and fields["tracking_period_end"] is not None)
        if has_tracking != has_tracking_window:
            raise FetchError(f"ETF {isin} tracking metrics require their full measurement period",
                             retryable=False)
        if (fields["tracking_period_start"] and fields["tracking_period_end"]
                and fields["tracking_period_end"] < fields["tracking_period_start"]):
            raise FetchError(f"ETF {isin} tracking measurement dates are reversed", retryable=False)
        for name in ("creation_units", "redemption_units", "creation_unit_size"):
            value = fields[name]
            if value is not None and (value < 0 or value != value.to_integral_value()):
                raise FetchError(f"ETF {isin} {name} must be a non-negative integer",
                                 retryable=False)
        has_flow = any(fields[name] is not None for name in
                       ("creation_units", "redemption_units"))
        for name in ("flow_period_start", "flow_period_end"):
            value = row.get(name, "")
            try:
                fields[name] = date.fromisoformat(value) if value else None
            except ValueError as exc:
                raise FetchError(f"ETF {isin} has invalid {name}", retryable=False) from exc
        has_flow_window = fields["flow_period_start"] is not None and fields["flow_period_end"] is not None
        if has_flow != has_flow_window:
            raise FetchError(f"ETF {isin} creation/redemption units require a full measurement period",
                             retryable=False)
        if has_flow and fields["creation_unit_size"] is None:
            raise FetchError(f"ETF {isin} creation/redemption units require creation_unit_size",
                             retryable=False)
        if fields["creation_unit_size"] is not None and fields["creation_unit_size"] <= 0:
            raise FetchError(f"ETF {isin} creation_unit_size must be positive", retryable=False)
        if (fields["flow_period_start"] and fields["flow_period_end"]
                and fields["flow_period_end"] < fields["flow_period_start"]):
            raise FetchError(f"ETF {isin} flow measurement dates are reversed", retryable=False)
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
               (fields["inception_date"], fields["listing_date"], fields["nav_as_of"],
                fields["expense_effective_from"], fields["expense_effective_to"],
                fields["tracking_period_start"], fields["tracking_period_end"],
                fields["flow_period_start"], fields["flow_period_end"])):
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
            "expense_ratio_pct": fields["expense_ratio_pct"],
            "expense_effective_from": fields["expense_effective_from"],
            "expense_effective_to": fields["expense_effective_to"],
            "tracking_error_pct": fields["tracking_error_pct"],
            "tracking_difference_pct": fields["tracking_difference_pct"],
            "tracking_period_start": fields["tracking_period_start"],
            "tracking_period_end": fields["tracking_period_end"],
            "creation_units": fields["creation_units"],
            "redemption_units": fields["redemption_units"],
            "creation_unit_size": fields["creation_unit_size"],
            "flow_period_start": fields["flow_period_start"],
            "flow_period_end": fields["flow_period_end"],
            "source_url": url,
            "source_document_sha256": document_hash,
            "source_published_at": published_at,
        })
    if not result:
        raise FetchError("ETF disclosure CSV contains no records", retryable=False)
    return result


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
