from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.config import Config
from financial_brain.forward_edge.data_permissions import (
    DataPermission, DataUse, PermissionStatus,
)
from financial_brain.ingest.mf_portfolio_disclosures import as_known_on, persist
from financial_brain.providers.mf_portfolio_disclosures import (
    MfDisclosureError, PortfolioAssetClass, PortfolioDisclosureRow,
)
from financial_brain.research.mf_portfolio_analytics import (
    analyze_portfolio, look_through_fof,
)
from financial_brain.storage.db import Database

SOURCE = "AMC"
HASH = "9" * 64
OBSERVED = datetime(2026, 5, 10, 10, tzinfo=timezone.utc)


def _row(scheme: str, asset_class: PortfolioAssetClass, weight: str, *,
         asset_id: str | None = None, **changes) -> PortfolioDisclosureRow:
    values = {
        "scheme_code": scheme, "plan": "DIRECT", "option": "GROWTH",
        "scheme_isin": f"INF{scheme}", "disclosure_date": date(2026, 4, 30),
        "asset_class": asset_class, "weight_percent": Decimal(weight),
        "asset_id": asset_id, "asset_name": asset_id, "sector": None,
        "credit_rating": None, "modified_duration_years": None,
        "yield_to_maturity_percent": None, "source_id": SOURCE,
        "source_ref": "portfolio-apr-2026", "source_sha256": HASH,
        "published_at": OBSERVED, "observed_at": OBSERVED,
    }
    values.update(changes)
    return PortfolioDisclosureRow(**values)


def _permissions(*uses: DataUse):
    return tuple(DataPermission(
        source_id=SOURCE, use=use, status=PermissionStatus.USE_ALLOWED,
        effective_from=date(2025, 1, 1), effective_through=None,
        evidence_ref="owner-approved-disclosure-rights", reviewer="data-owner",
    ) for use in uses)


def test_portfolio_metrics_expose_concentration_and_missing_field_coverage():
    rows = (
        _row("100001", PortfolioAssetClass.EQUITY, "40", asset_id="EQ1",
             sector="Financials"),
        _row("100001", PortfolioAssetClass.DEBT, "40", asset_id="BOND1",
             credit_rating="AAA", modified_duration_years=Decimal("2.5"),
             yield_to_maturity_percent=Decimal("7.2")),
        _row("100001", PortfolioAssetClass.FUND_OF_FUNDS, "10", asset_id="100002"),
        _row("100001", PortfolioAssetClass.UNKNOWN, "5"),
    )
    metrics = analyze_portfolio(rows)
    assert metrics.reported_weight_percent == Decimal("95")
    assert metrics.unreported_weight_percent == Decimal("5")
    assert metrics.unknown_asset_weight_percent == Decimal("5")
    assert metrics.sector_coverage_weight_percent == Decimal("40")
    assert metrics.debt_weight_percent == Decimal("40")
    assert metrics.duration_coverage_weight_percent == Decimal("40")
    assert metrics.weighted_modified_duration_years == Decimal("2.5")
    assert metrics.credit_rating_weights_percent == {"AAA": Decimal("40")}


def test_overweight_and_duplicate_rows_fail_closed():
    row = _row("100001", PortfolioAssetClass.EQUITY, "60", asset_id="EQ1")
    with pytest.raises(MfDisclosureError, match="exceed 100.5"):
        analyze_portfolio((row, _row("100001", PortfolioAssetClass.CASH, "41")))
    with pytest.raises(MfDisclosureError, match="duplicate"):
        analyze_portfolio((row, row))


def test_fof_expansion_consolidates_leaf_positions_without_counting_fund_units():
    root_rows = (
        _row("100001", PortfolioAssetClass.FUND_OF_FUNDS, "50", asset_id="100002"),
        _row("100001", PortfolioAssetClass.EQUITY, "50", asset_id="EQ-X"),
    )
    underlying_rows = (
        _row("100002", PortfolioAssetClass.EQUITY, "50", asset_id="EQ-X"),
        _row("100002", PortfolioAssetClass.DEBT, "50", asset_id="BOND-Y"),
    )
    result = look_through_fof("100001", {"100001": root_rows,
                                          "100002": underlying_rows})
    assert result.leaf_exposures_percent == {
        "BOND-Y": Decimal("25"), "EQ-X": Decimal("75"),
    }
    assert result.looked_through_weight_percent == Decimal("100")
    assert result.unlooked_through_weight_percent == Decimal(0)


def test_fof_reports_unavailable_underlying_and_rejects_cycles():
    root = (_row("100001", PortfolioAssetClass.FUND_OF_FUNDS, "80", asset_id="200002"),)
    missing = look_through_fof("100001", {"100001": root})
    assert missing.looked_through_weight_percent == 0
    assert missing.unlooked_through_weight_percent == Decimal("100")
    cyclic_root = (_row("100001", PortfolioAssetClass.FUND_OF_FUNDS,
                        "100", asset_id="200002"),)
    cyclic_child = (_row("200002", PortfolioAssetClass.FUND_OF_FUNDS,
                         "100", asset_id="100001"),)
    with pytest.raises(MfDisclosureError, match="cycle"):
        look_through_fof("100001", {"100001": cyclic_root, "200002": cyclic_child})


def test_disclosure_storage_is_permissioned_and_point_in_time(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    row = _row("100001", PortfolioAssetClass.EQUITY, "100", asset_id="EQ1")
    with db.connect() as con:
        with pytest.raises(MfDisclosureError, match="RETENTION"):
            persist(con, row, permissions=_permissions(DataUse.RESEARCH))
        persist(con, row, permissions=_permissions(DataUse.RESEARCH, DataUse.RETENTION))
        before = as_known_on(
            con, "100001", "DIRECT", "GROWTH", "INF100001",
            as_of_date=date(2026, 4, 30),
            decision_at=datetime(2026, 5, 9, 12, tzinfo=timezone.utc),
        )
        after = as_known_on(
            con, "100001", "DIRECT", "GROWTH", "INF100001",
            as_of_date=date(2026, 4, 30), decision_at=OBSERVED,
        )
        assert before == []
        assert len(after) == 1 and after[0]["asset_id"] == "EQ1"


def test_debt_metrics_are_rejected_on_equity_rows():
    with pytest.raises(MfDisclosureError, match="debt-only"):
        _row("100001", PortfolioAssetClass.EQUITY, "10", asset_id="EQ1",
             credit_rating="AAA")
