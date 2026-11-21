from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from financial_brain.forward_edge.data_permissions import (
    DataPermission,
    DataUse,
    PermissionStatus,
)
from financial_brain.providers.etf_reference import (
    ETFReferenceCatalog,
    ETFReferenceError,
    ETFReferenceRecord,
)


@pytest.fixture
def record() -> ETFReferenceRecord:
    return ETFReferenceRecord(
        source_id="NSE",
        exchange="NSE",
        symbol="INDEXETF",
        isin="INE000A01001",
        listing_date=date(2020, 1, 2),
        effective_from=date(2020, 1, 2),
        effective_to=None,
        series="EQ",
        published_at=datetime(2020, 1, 2, 3, tzinfo=timezone.utc),
        observed_at=datetime(2020, 1, 2, 4, tzinfo=timezone.utc),
        source_ref="NSE ETF reference file row 7",
        source_sha256="a" * 64,
        underlying_index="NIFTY 50 TRI",
        fund_house="Example AMC",
        provider_token="12345",
    )


def _permission(use: DataUse, *, status: PermissionStatus = PermissionStatus.USE_ALLOWED) -> DataPermission:
    return DataPermission(
        source_id="NSE",
        use=use,
        status=status,
        effective_from=(date(2020, 1, 1) if status is PermissionStatus.USE_ALLOWED else None),
        effective_through=None,
        evidence_ref="permission-register://nse-etf-reference",
        reviewer=("reviewer" if status is PermissionStatus.USE_ALLOWED else None),
    )


def _add(catalog: ETFReferenceCatalog, row: ETFReferenceRecord) -> None:
    catalog.add(
        row,
        retention_permission=_permission(DataUse.RETENTION),
        research_permission=_permission(DataUse.RESEARCH),
    )


def test_add_requires_distinct_retention_and_research_permissions(record: ETFReferenceRecord) -> None:
    catalog = ETFReferenceCatalog()
    with pytest.raises(ETFReferenceError, match="RETENTION permission"):
        catalog.add(
            record,
            retention_permission=_permission(DataUse.RETENTION, status=PermissionStatus.UNKNOWN_RIGHTS),
            research_permission=_permission(DataUse.RESEARCH),
        )

    with pytest.raises(ETFReferenceError, match="RESEARCH permission"):
        catalog.add(
            record,
            retention_permission=_permission(DataUse.RETENTION),
            research_permission=_permission(DataUse.RESEARCH, status=PermissionStatus.DENIED),
        )


def test_record_validates_identity_hash_and_clock_order(record: ETFReferenceRecord) -> None:
    assert record.instrument_id().asset_class == "ETF"
    assert record.instrument_id().underlying_index == "NIFTY 50 TRI"
    assert record.content_sha256() != record.source_sha256

    with pytest.raises(ETFReferenceError, match="12 alphanumeric"):
        ETFReferenceRecord(**{**record.__dict__, "isin": "bad"})
    with pytest.raises(ETFReferenceError, match="lowercase SHA-256"):
        ETFReferenceRecord(**{**record.__dict__, "source_sha256": "A" * 64})
    with pytest.raises(ETFReferenceError, match="observed before publication"):
        ETFReferenceRecord(
            **{
                **record.__dict__,
                "published_at": datetime(2020, 1, 2, 5, tzinfo=timezone.utc),
            }
        )


def test_resolve_requires_effective_and_published_point_in_time(record: ETFReferenceRecord) -> None:
    catalog = ETFReferenceCatalog()
    _add(catalog, record)

    assert catalog.resolve(
        exchange="nse", symbol="indexetf", as_of=datetime(2020, 1, 2, 5, tzinfo=timezone.utc)
    ) == record
    with pytest.raises(ETFReferenceError, match="unknown ETF identity"):
        catalog.resolve(
            exchange="NSE", symbol="INDEXETF", as_of=datetime(2020, 1, 2, 3, 30, tzinfo=timezone.utc)
        )
    with pytest.raises(ETFReferenceError, match="unknown ETF identity"):
        catalog.resolve(
            exchange="NSE", symbol="INDEXETF", as_of=datetime(2019, 12, 31, 23, tzinfo=timezone.utc)
        )


def test_effective_identity_overlap_is_rejected(record: ETFReferenceRecord) -> None:
    catalog = ETFReferenceCatalog()
    _add(catalog, record)
    successor = ETFReferenceRecord(
        **{
            **record.__dict__,
            "isin": "INE000A01019",
            "effective_from": date(2025, 1, 1),
            "listing_date": date(2020, 1, 2),
        }
    )
    with pytest.raises(ETFReferenceError, match="overlapping effective"):
        _add(catalog, successor)


def test_non_overlapping_symbol_successor_resolves_as_of_date(record: ETFReferenceRecord) -> None:
    catalog = ETFReferenceCatalog()
    _add(catalog, ETFReferenceRecord(**{**record.__dict__, "effective_to": date(2024, 12, 31)}))
    successor = ETFReferenceRecord(
        **{
            **record.__dict__,
            "isin": "INE000A01019",
            "effective_from": date(2025, 1, 1),
            "listing_date": date(2020, 1, 2),
        }
    )
    _add(catalog, successor)
    assert catalog.resolve(
        exchange="NSE", symbol="INDEXETF", as_of=datetime(2025, 1, 1, 5, tzinfo=timezone.utc)
    ).isin == "INE000A01019"
