"""Append-only ingestion and point-in-time lookup of MF economic-flow evidence."""
from __future__ import annotations

from datetime import datetime, timezone

from ..forward_edge.data_permissions import DataPermission
from ..kernel.trading_clock import IST
from ..providers.mf_scheme_flows import SchemeEconomicFlow, SchemeFlowError


def persist(con, record: SchemeEconomicFlow, *,
            permissions: tuple[DataPermission, ...]) -> str:
    record.require_permissions(permissions)
    con.execute("""INSERT INTO mf_scheme_flows (
        event_id, scheme_code, isin, kind, effective_date, cash_per_unit,
        reinvestment_nav, unit_ratio, successor_scheme_code, successor_isin, source_id,
        source_ref, source_sha256, published_at, observed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING""",
        [record.event_id, record.scheme_code, record.isin, record.kind.value,
         record.effective_date,
         str(record.cash_per_unit) if record.cash_per_unit is not None else None,
         str(record.reinvestment_nav) if record.reinvestment_nav is not None else None,
         str(record.unit_ratio) if record.unit_ratio is not None else None,
         record.successor_scheme_code, record.successor_isin,
         record.source_id, record.source_ref,
         record.source_sha256, record.published_at.astimezone(timezone.utc),
         record.observed_at.astimezone(timezone.utc)])
    return record.event_id


def as_known_on(con, scheme_code: str, isin: str, *, decision_at: datetime) -> list[dict]:
    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise ValueError("decision_at must be timezone-aware")
    rows = con.execute("""SELECT event_id, scheme_code, isin, kind, effective_date,
        cash_per_unit, reinvestment_nav, unit_ratio, successor_scheme_code, successor_isin,
        source_id, source_ref, source_sha256, published_at, observed_at
        FROM mf_scheme_flows WHERE scheme_code=? AND isin=?
          AND effective_date<=? AND published_at<=? AND observed_at<=?
        ORDER BY effective_date, published_at, observed_at, event_id""",
        [scheme_code, isin, decision_at.astimezone(IST).date(),
         decision_at.astimezone(timezone.utc), decision_at.astimezone(timezone.utc)]).fetchall()
    keys = ("event_id", "scheme_code", "isin", "kind", "effective_date",
            "cash_per_unit", "reinvestment_nav", "unit_ratio", "successor_scheme_code",
            "successor_isin", "source_id", "source_ref", "source_sha256", "published_at",
            "observed_at")
    if any(row[13] > decision_at.astimezone(timezone.utc)
           or row[14] > decision_at.astimezone(timezone.utc) for row in rows):
        raise SchemeFlowError("future event evidence leaked into as-known query")
    return [dict(zip(keys, row, strict=True)) for row in rows]


__all__ = ["as_known_on", "persist"]
