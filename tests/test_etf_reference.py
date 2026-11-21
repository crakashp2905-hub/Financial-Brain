from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from financial_brain.forward_edge.data_permissions import (
    DataPermission,
    DataUse,
    PermissionStatus,
)
from financial_brain.providers.etf_reference import (
    ETFExposureCategory,
    ETFPrimaryMarketContext,
    ETFReferenceCatalog,
    ETFReferenceError,
    ETFReferenceRecord,
    ETFTrackingMetrics,
    ETFValuationObservation,
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


def _permission(use: DataUse, *, source_id: str = "NSE",
                status: PermissionStatus = PermissionStatus.USE_ALLOWED) -> DataPermission:
    return DataPermission(
        source_id=source_id,
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


@pytest.mark.parametrize(
    "category",
    [
        "EQUITY_BROAD_MARKET",
        "EQUITY_SECTOR",
        "EQUITY_FACTOR",
        "BOND_DEBT",
        "GOLD",
        "SILVER",
        "INTERNATIONAL",
        "OTHER_LISTED",
    ],
)
def test_exposure_categories_are_distinct_and_explicit(category: str, record: ETFReferenceRecord) -> None:
    normalized = ETFReferenceRecord(**{**record.__dict__, "exposure_category": category})
    assert normalized.exposure_category is ETFExposureCategory(category)


def test_symbol_name_does_not_infer_exposure_category(record: ETFReferenceRecord) -> None:
    gold_named = ETFReferenceRecord(**{**record.__dict__, "symbol": "GOLDTRACKER"})
    assert gold_named.exposure_category is ETFExposureCategory.UNKNOWN
    with pytest.raises(ETFReferenceError, match="explicit and recognized"):
        ETFReferenceRecord(**{**record.__dict__, "exposure_category": "PRECIOUS_METAL"})


def test_nav_and_inav_are_source_hashed_and_publication_time_gated(
    record: ETFReferenceRecord,
) -> None:
    catalog = ETFReferenceCatalog()
    _add(catalog, record)
    observation = ETFValuationObservation(
        source_id="NSE",
        exchange="NSE",
        symbol="INDEXETF",
        isin="INE000A01001",
        valuation_type="INAV",
        valuation_date=date(2020, 1, 2),
        value_per_unit="101.25",
        published_at=datetime(2020, 1, 2, 6, tzinfo=timezone.utc),
        observed_at=datetime(2020, 1, 2, 7, tzinfo=timezone.utc),
        source_ref="NSE iNAV observation",
        source_sha256="b" * 64,
    )
    catalog.add_valuation(
        observation,
        retention_permission=_permission(DataUse.RETENTION),
        research_permission=_permission(DataUse.RESEARCH),
    )

    with pytest.raises(ETFReferenceError, match="no published ETF valuation"):
        catalog.resolve_valuation(
            exchange="NSE", symbol="INDEXETF", isin="INE000A01001", valuation_type="INAV",
            valuation_date=date(2020, 1, 2), as_of=datetime(2020, 1, 2, 6, 30, tzinfo=timezone.utc),
        )
    resolved = catalog.resolve_valuation(
        exchange="NSE", symbol="INDEXETF", isin="INE000A01001", valuation_type="INAV",
        valuation_date=date(2020, 1, 2), as_of=datetime(2020, 1, 2, 8, tzinfo=timezone.utc),
    )
    assert resolved.value_per_unit.as_tuple().digits == (1, 0, 1, 2, 5)
    assert len(resolved.content_sha256()) == 64


def test_valuation_cannot_cross_point_in_time_listing_identity(record: ETFReferenceRecord) -> None:
    catalog = ETFReferenceCatalog()
    _add(catalog, record)
    row = ETFValuationObservation(
        source_id="NSE", exchange="NSE", symbol="INDEXETF", isin="INE999A01019",
        valuation_type="NAV", valuation_date=date(2020, 1, 2), value_per_unit="10",
        published_at=datetime(2020, 1, 2, 6, tzinfo=timezone.utc),
        observed_at=datetime(2020, 1, 2, 7, tzinfo=timezone.utc), source_ref="wrong ISIN",
        source_sha256="c" * 64,
    )
    with pytest.raises(ETFReferenceError, match="does not match"):
        catalog.add_valuation(
            row,
            retention_permission=_permission(DataUse.RETENTION),
            research_permission=_permission(DataUse.RESEARCH),
        )


def test_inception_date_cannot_follow_listing_date(record: ETFReferenceRecord) -> None:
    with pytest.raises(ETFReferenceError, match="inception cannot follow"):
        ETFReferenceRecord(**{**record.__dict__, "inception_date": date(2020, 1, 3)})


def test_tracking_error_and_expense_ratio_are_effective_and_publication_time_gated(
    record: ETFReferenceRecord,
) -> None:
    catalog = ETFReferenceCatalog()
    _add(catalog, record)
    metrics = ETFTrackingMetrics(
        source_id="AMC", exchange="NSE", symbol="INDEXETF", isin=record.isin,
        effective_from=date(2026, 4, 1), effective_to=None,
        tracking_difference_bps="-14.5", tracking_error_bps="22.0", expense_ratio_bps="5.0",
        published_at=datetime(2026, 11, 12, 10, tzinfo=timezone.utc),
        observed_at=datetime(2026, 11, 12, 10, 1, tzinfo=timezone.utc),
        source_ref="AMC monthly tracking report", source_sha256="d" * 64,
    )
    catalog.add_tracking_metrics(
        metrics,
        retention_permission=_permission(DataUse.RETENTION, source_id="AMC"),
        research_permission=_permission(DataUse.RESEARCH, source_id="AMC"),
    )
    with pytest.raises(ETFReferenceError, match="no published"):
        catalog.resolve_tracking_metrics(
            exchange="NSE", symbol="INDEXETF", isin=record.isin,
            as_of=datetime(2026, 11, 12, 10, tzinfo=timezone.utc),
        )
    resolved = catalog.resolve_tracking_metrics(
        exchange="NSE", symbol="INDEXETF", isin=record.isin,
        as_of=datetime(2026, 11, 12, 11, tzinfo=timezone.utc),
    )
    assert resolved.tracking_difference_bps == -14.5
    assert resolved.tracking_error_bps == 22
    assert resolved.expense_ratio_bps == 5
    assert len(resolved.content_sha256()) == 64


def test_tracking_error_and_expense_ratio_cannot_be_negative(record: ETFReferenceRecord) -> None:
    with pytest.raises(ETFReferenceError, match="cannot be negative"):
        ETFTrackingMetrics(
            source_id="NSE", exchange="NSE", symbol=record.symbol, isin=record.isin,
            effective_from=date(2026, 1, 1), effective_to=None,
            tracking_difference_bps="-1", tracking_error_bps="-2", expense_ratio_bps="0",
            published_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            observed_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            source_ref="notice", source_sha256="e" * 64,
        )


def test_creation_redemption_records_are_context_only_and_publication_gated(
    record: ETFReferenceRecord,
) -> None:
    catalog = ETFReferenceCatalog()
    _add(catalog, record)
    context = ETFPrimaryMarketContext(
        source_id="NSE", exchange="NSE", symbol=record.symbol, isin=record.isin,
        activity="REDEMPTION", context_date=date(2026, 11, 12), quantity="50000",
        quantity_unit="UNITS", published_at=datetime(2026, 11, 13, 4, tzinfo=timezone.utc),
        observed_at=datetime(2026, 11, 13, 4, 1, tzinfo=timezone.utc),
        source_ref="exchange aggregate context", source_sha256="f" * 64,
    )
    catalog.add_primary_market_context(
        context,
        retention_permission=_permission(DataUse.RETENTION),
        research_permission=_permission(DataUse.RESEARCH),
    )
    assert context.retail_order_actionable is False
    assert context.use_label == "ISSUER_MARKET_MAKER_REFERENCE_CONTEXT_ONLY"
    assert catalog.primary_market_context_as_of(
        exchange="NSE", symbol=record.symbol, isin=record.isin,
        as_of=datetime(2026, 11, 13, 3, 59, tzinfo=timezone.utc),
    ) == []
    assert catalog.primary_market_context_as_of(
        exchange="NSE", symbol=record.symbol, isin=record.isin,
        as_of=datetime(2026, 11, 13, 5, tzinfo=timezone.utc),
    ) == [context]


def test_primary_market_flow_does_not_accept_retail_order_semantics(record: ETFReferenceRecord) -> None:
    with pytest.raises(ETFReferenceError, match="activity must be"):
        ETFPrimaryMarketContext(
            source_id="NSE", exchange="NSE", symbol=record.symbol, isin=record.isin,
            activity="RETAIL_REDEEM_NOW", context_date=date(2026, 11, 12), quantity="1",
            quantity_unit="UNITS", published_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
            observed_at=datetime(2026, 11, 13, 1, tzinfo=timezone.utc),
            source_ref="source", source_sha256="f" * 64,
        )
