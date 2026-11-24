"""Persist append-only MF scheme lifecycle evidence and resolve it point in time."""
from __future__ import annotations

from datetime import date, datetime, timezone

from ..forward_edge.data_permissions import DataPermission
from ..kernel.trading_clock import IST
from ..providers.mf_scheme_lifecycle import SchemeLifecycleError, SchemeLifecycleRecord


def persist(con, record: SchemeLifecycleRecord, *,
            permissions: tuple[DataPermission, ...]) -> str:
    """Append a source-hashed lifecycle assertion after checking retention rights."""
    record.require_permissions(permissions)
    con.execute("""INSERT INTO mf_scheme_lifecycle (
        record_id, scheme_code, plan, option, isin, lifecycle, effective_from,
        effective_to, successor_scheme_code, source_id, source_ref, source_sha256,
        published_at, observed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(record_id) DO NOTHING""",
        [record.record_id, record.scheme_code, record.plan, record.option, record.isin,
         record.lifecycle.value, record.effective_from, record.effective_to,
         record.successor_scheme_code, record.source_id, record.source_ref,
         record.source_sha256, record.published_at.astimezone(timezone.utc),
         record.observed_at.astimezone(timezone.utc)])
    return record.record_id


def as_known_on(con, scheme_code: str, plan: str, option: str, isin: str, *,
                decision_date: date, decision_at: datetime) -> dict | None:
    """Resolve lifecycle effective on a date and observable by the decision clock."""
    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise ValueError("decision_at must be timezone-aware")
    if not all(value.strip() for value in (scheme_code, plan, option, isin)):
        raise ValueError("scheme identity fields are required")
    local_date = decision_at.astimezone(IST).date()
    if decision_date > local_date:
        raise ValueError("decision_date cannot be after decision_at in India")
    rows = con.execute("""SELECT record_id, scheme_code, plan, option, isin, lifecycle,
        effective_from, effective_to, successor_scheme_code, source_id, source_ref,
        source_sha256, published_at, observed_at
        FROM mf_scheme_lifecycle
        WHERE scheme_code=? AND plan=? AND option=? AND isin=?
          AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)
          AND published_at<=? AND observed_at<=?
        ORDER BY published_at DESC, observed_at DESC, record_id DESC LIMIT 2""",
        [scheme_code, plan, option, isin, decision_date, decision_date,
         decision_at.astimezone(timezone.utc), decision_at.astimezone(timezone.utc)]).fetchall()
    if not rows:
        return None
    if len(rows) > 1 and rows[0][12:14] == rows[1][12:14]:
        raise SchemeLifecycleError("ambiguous lifecycle evidence at decision timestamp")
    keys = ("record_id", "scheme_code", "plan", "option", "isin", "lifecycle",
            "effective_from", "effective_to", "successor_scheme_code", "source_id",
            "source_ref", "source_sha256", "published_at", "observed_at")
    return dict(zip(keys, rows[0], strict=True))


__all__ = ["as_known_on", "persist"]
