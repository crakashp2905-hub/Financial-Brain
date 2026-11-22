"""Persist effective-dated MF costs and resolve only decision-time-known versions."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

from ..forward_edge.data_permissions import DataPermission
from ..kernel.trading_clock import IST
from ..providers.mf_scheme_terms import SchemeCommercialTerms, SchemeTermsError


def persist(con, record: SchemeCommercialTerms, *,
            permissions: tuple[DataPermission, ...]) -> str:
    record.require_permissions(permissions)
    exit_loads = None if record.exit_loads is None else json.dumps([
        {"days_from_purchase": band.days_from_purchase,
         "rate_percent": str(band.rate_percent)} for band in record.exit_loads
    ], sort_keys=True, separators=(",", ":"))
    restrictions = None if record.exit_restrictions is None else json.dumps(
        [item.value for item in record.exit_restrictions], separators=(",", ":")
    )
    con.execute("""INSERT INTO mf_scheme_commercial_terms (
        record_id, scheme_code, plan, option, isin, effective_from, effective_to,
        ter_percent, aum_inr, exit_loads_json, minimum_initial_inr,
        minimum_additional_inr, minimum_redemption_inr, exit_restrictions_json,
        source_id, source_ref,
        source_sha256, published_at, observed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(record_id) DO NOTHING""",
        [record.record_id, record.scheme_code, record.plan, record.option, record.isin,
         record.effective_from, record.effective_to,
         str(record.ter_percent) if record.ter_percent is not None else None,
         str(record.aum_inr) if record.aum_inr is not None else None,
         exit_loads,
         str(record.minimum_initial_inr) if record.minimum_initial_inr is not None else None,
         str(record.minimum_additional_inr)
         if record.minimum_additional_inr is not None else None,
         str(record.minimum_redemption_inr)
         if record.minimum_redemption_inr is not None else None,
         restrictions, record.source_id, record.source_ref, record.source_sha256,
         record.published_at.astimezone(timezone.utc),
         record.observed_at.astimezone(timezone.utc)])
    return record.record_id


def as_known_on(con, scheme_code: str, plan: str, option: str, isin: str, *,
                decision_date: date, decision_at: datetime) -> dict | None:
    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise ValueError("decision_at must be timezone-aware")
    if not all(value.strip() for value in (scheme_code, plan, option, isin)):
        raise ValueError("scheme identity fields are required")
    if decision_date > decision_at.astimezone(IST).date():
        raise ValueError("decision_date cannot be after decision_at in India")
    rows = con.execute("""SELECT record_id, scheme_code, plan, option, isin,
        effective_from, effective_to, ter_percent, aum_inr, exit_loads_json,
        minimum_initial_inr, minimum_additional_inr, minimum_redemption_inr,
        exit_restrictions_json,
        source_id, source_ref, source_sha256, published_at, observed_at
        FROM mf_scheme_commercial_terms
        WHERE scheme_code=? AND plan=? AND option=? AND isin=?
          AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)
          AND published_at<=? AND observed_at<=?
        ORDER BY published_at DESC, observed_at DESC, record_id DESC LIMIT 2""",
        [scheme_code, plan, option, isin, decision_date, decision_date,
         decision_at.astimezone(timezone.utc), decision_at.astimezone(timezone.utc)]).fetchall()
    if not rows:
        return None
    if len(rows) > 1 and rows[0][17:19] == rows[1][17:19]:
        raise SchemeTermsError("ambiguous commercial terms at decision timestamp")
    keys = ("record_id", "scheme_code", "plan", "option", "isin", "effective_from",
            "effective_to", "ter_percent", "aum_inr", "exit_loads_json",
            "minimum_initial_inr", "minimum_additional_inr", "minimum_redemption_inr",
            "exit_restrictions_json",
            "source_id", "source_ref", "source_sha256", "published_at", "observed_at")
    result = dict(zip(keys, rows[0], strict=True))
    for name in ("ter_percent", "aum_inr", "minimum_initial_inr", "minimum_additional_inr",
                 "minimum_redemption_inr"):
        if result[name] is not None:
            result[name] = Decimal(result[name])
    for name in ("exit_loads_json", "exit_restrictions_json"):
        if result[name] is not None:
            result[name] = json.loads(result[name])
    return result


__all__ = ["as_known_on", "persist"]
