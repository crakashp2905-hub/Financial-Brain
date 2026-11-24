"""Append-only persistence and as-known lookup for MF portfolio disclosures."""
from __future__ import annotations

from datetime import date, datetime, timezone

from ..forward_edge.data_permissions import DataPermission
from ..kernel.trading_clock import IST
from ..providers.mf_portfolio_disclosures import PortfolioDisclosureRow


def persist(con, row: PortfolioDisclosureRow, *,
            permissions: tuple[DataPermission, ...]) -> str:
    row.require_permissions(permissions)
    con.execute("""INSERT INTO mf_portfolio_disclosures (
        record_id, scheme_code, plan, option, scheme_isin, disclosure_date,
        asset_class, weight_percent, asset_id, asset_name, sector, credit_rating,
        modified_duration_years, yield_to_maturity_percent, source_id, source_ref,
        source_sha256, published_at, observed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(record_id) DO NOTHING""",
        [row.record_id, row.scheme_code, row.plan, row.option, row.scheme_isin,
         row.disclosure_date, row.asset_class.value, str(row.weight_percent), row.asset_id,
         row.asset_name, row.sector, row.credit_rating,
         str(row.modified_duration_years) if row.modified_duration_years is not None else None,
         str(row.yield_to_maturity_percent)
         if row.yield_to_maturity_percent is not None else None,
         row.source_id, row.source_ref, row.source_sha256,
         row.published_at.astimezone(timezone.utc), row.observed_at.astimezone(timezone.utc)])
    return row.record_id


def as_known_on(con, scheme_code: str, plan: str, option: str, scheme_isin: str, *,
                as_of_date: date, decision_at: datetime) -> list[dict]:
    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise ValueError("decision_at must be timezone-aware")
    if not all(value.strip() for value in (scheme_code, plan, option, scheme_isin)):
        raise ValueError("scheme identity fields are required")
    if as_of_date > decision_at.astimezone(IST).date():
        raise ValueError("as_of_date cannot be after decision_at in India")
    known_at = decision_at.astimezone(timezone.utc)
    rows = con.execute("""WITH available AS (
        SELECT *, MAX(disclosure_date) OVER () AS latest_disclosure_date
        FROM mf_portfolio_disclosures
        WHERE scheme_code=? AND plan=? AND option=? AND scheme_isin=?
          AND disclosure_date<=? AND published_at<=? AND observed_at<=?
    ), latest AS (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY asset_class, COALESCE(asset_id, record_id)
            ORDER BY published_at DESC, observed_at DESC, record_id DESC) AS revision_rank
        FROM available WHERE disclosure_date=latest_disclosure_date
    )
        SELECT record_id, scheme_code, plan, option, scheme_isin, disclosure_date,
               asset_class, weight_percent, asset_id, asset_name, sector, credit_rating,
               modified_duration_years, yield_to_maturity_percent, source_id, source_ref,
               source_sha256, published_at, observed_at
        FROM latest WHERE revision_rank=1
        ORDER BY asset_class, asset_id, record_id""",
        [scheme_code, plan, option, scheme_isin, as_of_date, known_at, known_at]).fetchall()
    keys = ("record_id", "scheme_code", "plan", "option", "scheme_isin",
            "disclosure_date", "asset_class", "weight_percent", "asset_id", "asset_name",
            "sector", "credit_rating", "modified_duration_years",
            "yield_to_maturity_percent", "source_id", "source_ref", "source_sha256",
            "published_at", "observed_at")
    return [dict(zip(keys, row, strict=True)) for row in rows]


__all__ = ["as_known_on", "persist"]
