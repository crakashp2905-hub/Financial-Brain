"""Persist and point-in-time resolve source-backed MF scheme-master versions."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from ..providers.mf_scheme_master import SchemeMasterError, SchemeMasterRecord
from ..forward_edge.data_permissions import DataPermission


def _record_payload(record: SchemeMasterRecord) -> dict[str, object]:
    return {
        "scheme_code": record.scheme_code, "plan": record.plan.value,
        "option": record.option.value, "isin": record.isin,
        "effective_from": record.effective_from.isoformat(),
        "effective_to": record.effective_to.isoformat() if record.effective_to else None,
        "structure": record.structure.value,
        "investment_style": record.investment_style.value,
        "asset_category": record.asset_category.value,
        "category_label": record.category_label, "fund_house": record.fund_house,
        "riskometer": record.riskometer, "benchmark_tri": record.benchmark_tri,
        "manager": record.manager, "objective": record.objective,
        "source_id": record.source_id, "source_ref": record.source_ref,
        "source_sha256": record.source_sha256,
        "published_at": record.published_at.isoformat(),
        "observed_at": record.observed_at.isoformat(),
    }


def persist(con, record: SchemeMasterRecord, *,
            permissions: tuple[DataPermission, ...]) -> str:
    """Append a source version after permission and interval-conflict checks."""
    record.require_permissions(permissions)
    payload = _record_payload(record)
    record_id = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    existing = con.execute("""SELECT record_id, effective_from, effective_to
        FROM mf_scheme_master WHERE scheme_code=? AND plan=? AND option=? AND isin=?""",
        [record.scheme_code, record.plan.value, record.option.value, record.isin]).fetchall()
    for old_id, old_from, old_to in existing:
        if old_id == record_id:
            return record_id
        overlaps = ((old_to is None or old_to >= record.effective_from)
                    and (record.effective_to is None or old_from <= record.effective_to))
        if overlaps:
            raise SchemeMasterError("overlapping effective scheme-master versions")
    con.execute("""INSERT INTO mf_scheme_master (
        record_id, scheme_code, plan, option, isin, effective_from, effective_to,
        structure, investment_style, asset_category, category_label, fund_house,
        riskometer, benchmark_tri, manager, objective, source_id, source_ref,
        source_sha256, published_at, observed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [record_id, record.scheme_code, record.plan.value, record.option.value, record.isin,
         record.effective_from, record.effective_to, record.structure.value,
         record.investment_style.value, record.asset_category.value, record.category_label,
         record.fund_house, record.riskometer, record.benchmark_tri, record.manager,
         record.objective, record.source_id, record.source_ref, record.source_sha256,
         record.published_at.astimezone(timezone.utc),
         record.observed_at.astimezone(timezone.utc)])
    return record_id


def as_known_on(con, scheme_code: str, plan: str, option: str, isin: str, *,
                decision_at: datetime) -> dict | None:
    """Resolve one effective and already-published version; ambiguity fails closed."""
    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise SchemeMasterError("decision_at must be timezone-aware")
    local_date = decision_at.date()
    rows = con.execute("""SELECT record_id, scheme_code, plan, option, isin,
        effective_from, effective_to, structure, investment_style, asset_category,
        category_label, fund_house, riskometer, benchmark_tri, manager, objective,
        source_id, source_ref, source_sha256, published_at, observed_at
        FROM mf_scheme_master
        WHERE scheme_code=? AND plan=? AND option=? AND isin=?
          AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)
          AND published_at<=? AND observed_at<=?
        ORDER BY published_at DESC, observed_at DESC, source_sha256 DESC LIMIT 2""",
        [scheme_code, plan, option, isin, local_date, local_date,
         decision_at.astimezone(timezone.utc), decision_at.astimezone(timezone.utc)]).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        raise SchemeMasterError("ambiguous scheme-master version for decision timestamp")
    fields = ("record_id", "scheme_code", "plan", "option", "isin", "effective_from",
              "effective_to", "structure", "investment_style", "asset_category",
              "category_label", "fund_house", "riskometer", "benchmark_tri", "manager",
              "objective", "source_id", "source_ref", "source_sha256", "published_at",
              "observed_at")
    return dict(zip(fields, rows[0], strict=True))


__all__ = ["as_known_on", "persist"]
