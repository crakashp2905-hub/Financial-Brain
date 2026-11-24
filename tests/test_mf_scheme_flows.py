from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.config import Config
from financial_brain.forward_edge.data_permissions import (
    DataPermission, DataUse, PermissionStatus,
)
from financial_brain.ingest.mf_scheme_flows import as_known_on, persist
from financial_brain.paper.mf_scheme_flows import MfPaperPosition, apply_flow
from financial_brain.providers.mf_scheme_flows import (
    SchemeEconomicFlow, SchemeFlowError, SchemeFlowKind,
)
from financial_brain.storage.db import Database

SOURCE = "AMC"
HASH = "f" * 64
DECISION = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)


def _permissions(*uses: DataUse):
    return tuple(DataPermission(
        source_id=SOURCE, use=use, status=PermissionStatus.USE_ALLOWED,
        effective_from=date(2025, 1, 1), effective_through=None,
        evidence_ref="owner-approved-terms", reviewer="data-owner",
    ) for use in uses)


def _flow(kind: SchemeFlowKind, **changes) -> SchemeEconomicFlow:
    values = {
        "scheme_code": "100001", "isin": "INF000000001", "kind": kind,
        "effective_date": date(2026, 1, 3), "cash_per_unit": None,
        "reinvestment_nav": None, "unit_ratio": None,
        "successor_scheme_code": None, "successor_isin": None,
        "source_id": SOURCE, "source_ref": "amc-circular-2026-01",
        "source_sha256": HASH,
        "published_at": datetime(2026, 1, 4, tzinfo=timezone.utc),
        "observed_at": datetime(2026, 1, 4, 1, tzinfo=timezone.utc),
    }
    values.update(changes)
    return SchemeEconomicFlow(**values)


def test_idcw_cash_payout_reconciles_the_nav_drop_and_is_idempotent():
    event = _flow(SchemeFlowKind.IDCW_PAYOUT, cash_per_unit=Decimal("10"))
    position = MfPaperPosition("100001", "INF000000001", Decimal("10"))
    first = apply_flow(position, event, decision_at=DECISION,
                       nav_before=100, nav_after=90,
                       permissions=_permissions(DataUse.RESEARCH, DataUse.RETENTION))
    assert first.event_applied
    assert first.position.cash_inr == Decimal("100")
    assert first.position.units == Decimal("10")
    assert first.value_before_inr == first.value_after_inr == Decimal("1000")
    assert first.total_return_percent == 0
    second = apply_flow(first.position, event, decision_at=DECISION,
                        nav_before=90, nav_after=90,
                        permissions=_permissions(DataUse.RESEARCH, DataUse.RETENTION))
    assert not second.event_applied
    assert second.position == first.position


def test_idcw_reinvestment_and_unit_split_preserve_total_value():
    permissions = _permissions(DataUse.RESEARCH, DataUse.RETENTION)
    position = MfPaperPosition("100001", "INF000000001", Decimal("10"))
    reinvest = _flow(SchemeFlowKind.IDCW_REINVESTMENT,
                     cash_per_unit=Decimal("10"), reinvestment_nav=Decimal("90"))
    result = apply_flow(position, reinvest, decision_at=DECISION,
                        nav_before=100, nav_after=90, permissions=permissions)
    assert result.position.units == Decimal("10") + Decimal(100) / Decimal(90)
    assert result.value_after_inr == Decimal("1000")
    split = _flow(SchemeFlowKind.UNIT_SPLIT, unit_ratio=Decimal("2"))
    split_result = apply_flow(position, split, decision_at=DECISION,
                              nav_before=100, nav_after=50, permissions=permissions)
    assert split_result.position.units == Decimal("20")
    assert split_result.value_after_inr == Decimal("1000")


def test_scheme_merger_uses_explicit_successor_identity_and_conversion_ratio():
    merger = _flow(
        SchemeFlowKind.SCHEME_MERGER, unit_ratio=Decimal("0.5"),
        successor_scheme_code="200002", successor_isin="INF000000002",
    )
    result = apply_flow(
        MfPaperPosition("100001", "INF000000001", Decimal("10")), merger,
        decision_at=DECISION, nav_before=100, nav_after=200,
        permissions=_permissions(DataUse.RESEARCH, DataUse.RETENTION),
    )
    assert (result.position.scheme_code, result.position.isin) == (
        "200002", "INF000000002",
    )
    assert result.position.units == Decimal("5")
    assert result.value_after_inr == Decimal("1000")


def test_flow_cannot_be_used_before_observation_or_without_retention_rights():
    flow = _flow(SchemeFlowKind.IDCW_PAYOUT, cash_per_unit=Decimal("1"))
    with pytest.raises(SchemeFlowError, match="not yet available"):
        apply_flow(
            MfPaperPosition("100001", "INF000000001", Decimal("1")), flow,
            decision_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            nav_before=10, nav_after=9,
            permissions=_permissions(DataUse.RESEARCH, DataUse.RETENTION),
        )
    with pytest.raises(SchemeFlowError, match="RETENTION"):
        flow.require_permissions(_permissions(DataUse.RESEARCH))


def test_append_only_flow_ingestion_is_permission_gated_and_as_known(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    flow = _flow(SchemeFlowKind.IDCW_PAYOUT, cash_per_unit=Decimal("1"))
    with db.connect() as con:
        with pytest.raises(SchemeFlowError, match="RETENTION"):
            persist(con, flow, permissions=_permissions(DataUse.RESEARCH))
        assert persist(con, flow, permissions=_permissions(
            DataUse.RESEARCH, DataUse.RETENTION)) == flow.event_id
        assert persist(con, flow, permissions=_permissions(
            DataUse.RESEARCH, DataUse.RETENTION)) == flow.event_id
        assert as_known_on(
            con, "100001", "INF000000001",
            decision_at=datetime(2026, 1, 3, 12, tzinfo=timezone.utc),
        ) == []
        rows = as_known_on(con, "100001", "INF000000001", decision_at=DECISION)
        assert len(rows) == 1
        assert rows[0]["event_id"] == flow.event_id
        assert rows[0]["cash_per_unit"] == "1"
        assert con.execute("SELECT count(*) FROM mf_scheme_flows").fetchone()[0] == 1


def test_merger_requires_explicit_successor_code_and_isin():
    with pytest.raises(SchemeFlowError, match="successor_scheme_code"):
        _flow(SchemeFlowKind.SCHEME_MERGER, unit_ratio=Decimal("1"))
    with pytest.raises(SchemeFlowError, match="successor_isin"):
        _flow(SchemeFlowKind.SCHEME_MERGER,
              unit_ratio=Decimal("1"), successor_scheme_code="200002")
