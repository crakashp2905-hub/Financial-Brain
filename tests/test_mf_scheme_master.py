from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from financial_brain.config import Config
from financial_brain.forward_edge.data_permissions import (
    DataPermission,
    DataUse,
    PermissionStatus,
)
from financial_brain.ingest.mf_scheme_master import as_known_on, persist
from financial_brain.providers.mf_scheme_master import (
    AssetCategory,
    InvestmentStyle,
    OptionType,
    PlanType,
    SchemeMasterError,
    SchemeMasterRecord,
    SchemeStructure,
)
from financial_brain.storage.db import Database

HASH = "a" * 64
SOURCE = "AMFI"
OBSERVED = datetime(2026, 11, 10, 10, tzinfo=timezone.utc)


def _record(**changes) -> SchemeMasterRecord:
    values = {
        "scheme_code": "100001", "plan": PlanType.DIRECT,
        "option": OptionType.GROWTH, "isin": "INF000000001",
        "effective_from": date(2026, 1, 1), "effective_to": date(2026, 6, 30),
        "structure": SchemeStructure.OPEN_ENDED,
        "investment_style": InvestmentStyle.ACTIVE,
        "asset_category": AssetCategory.EQUITY,
        "category_label": "Small Cap Fund", "fund_house": "Example AMC",
        "riskometer": "Very High", "benchmark_tri": "Nifty Smallcap 250 TRI",
        "manager": "Manager A", "objective": "Long-term capital appreciation",
        "source_id": SOURCE, "source_ref": "amfi-circular-2026-01",
        "source_sha256": HASH,
        "published_at": datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
        "observed_at": OBSERVED,
    }
    values.update(changes)
    return SchemeMasterRecord(**values)


def _permissions() -> tuple[DataPermission, ...]:
    common = {
        "source_id": SOURCE, "status": PermissionStatus.USE_ALLOWED,
        "effective_from": date(2026, 1, 1), "effective_through": None,
        "evidence_ref": "owner-reviewed-source-terms", "reviewer": "data-owner",
    }
    return (
        DataPermission(use=DataUse.RESEARCH, **common),
        DataPermission(use=DataUse.RETENTION, **common),
    )


def _database(tmp_path):
    cfg = Config(data_root=tmp_path).ensure()
    db = Database(cfg)
    db.migrate()
    return db


def test_effective_dated_updates_preserve_history_and_publication_availability(tmp_path):
    db = _database(tmp_path)
    old = _record(observed_at=datetime(2026, 1, 3, tzinfo=timezone.utc))
    new = _record(
        effective_from=date(2026, 7, 1), effective_to=None,
        category_label="Mid Cap Fund", benchmark_tri="Nifty Midcap 150 TRI",
        manager="Manager B", published_at=datetime(2026, 7, 1, 8, tzinfo=timezone.utc),
        observed_at=datetime(2026, 7, 1, 9, tzinfo=timezone.utc),
    )
    with db.connect() as con:
        old_id = persist(con, old, permissions=_permissions())
        assert persist(con, old, permissions=_permissions()) == old_id
        persist(con, new, permissions=_permissions())

        before_notice = as_known_on(
            con, "100001", "DIRECT", "GROWTH", "INF000000001",
            decision_at=datetime(2026, 6, 30, 12, tzinfo=timezone.utc),
        )
        after_notice = as_known_on(
            con, "100001", "DIRECT", "GROWTH", "INF000000001",
            decision_at=datetime(2026, 7, 2, 12, tzinfo=timezone.utc),
        )
        before_any_observation = as_known_on(
            con, "100001", "DIRECT", "GROWTH", "INF000000001",
            decision_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        assert before_notice is not None and before_notice["category_label"] == "Small Cap Fund"
        assert after_notice is not None and after_notice["category_label"] == "Mid Cap Fund"
        assert before_any_observation is None
        assert con.execute("SELECT count(*) FROM mf_scheme_master").fetchone()[0] == 2


def test_plan_option_and_isin_are_all_part_of_the_canonical_identity(tmp_path):
    db = _database(tmp_path)
    direct = _record()
    regular_idcw = _record(
        plan=PlanType.REGULAR, option=OptionType.IDCW_PAYOUT,
        isin="INF000000002", source_ref="sid-classification-2026-01",
        effective_to=None,
    )
    with db.connect() as con:
        persist(con, direct, permissions=_permissions())
        persist(con, regular_idcw, permissions=_permissions())
        result = as_known_on(
            con, "100001", "REGULAR", "IDCW_PAYOUT", "INF000000002",
            decision_at=OBSERVED,
        )
        assert result is not None
        assert result["plan"] == "REGULAR" and result["option"] == "IDCW_PAYOUT"


def test_research_and_retention_permissions_are_independently_required():
    record = _record()
    research_only = (DataPermission(
        source_id=SOURCE, use=DataUse.RESEARCH,
        status=PermissionStatus.USE_ALLOWED, effective_from=date(2026, 1, 1),
        effective_through=None, evidence_ref="research-terms", reviewer="data-owner",
    ),)
    with pytest.raises(SchemeMasterError, match="RETENTION permission"):
        record.require_permissions(research_only)
    with pytest.raises(SchemeMasterError, match="RESEARCH permission"):
        record.require_permissions(())


def test_unknown_rights_and_effective_interval_overlap_fail_closed(tmp_path):
    db = _database(tmp_path)
    record = _record()
    unknown = (
        DataPermission(SOURCE, DataUse.RESEARCH, PermissionStatus.UNKNOWN_RIGHTS,
                       None, None, "terms-not-reviewed", None),
        *_permissions()[1:],
    )
    with pytest.raises(SchemeMasterError, match="RESEARCH permission"):
        record.require_permissions(unknown)

    overlap = replace(record, effective_from=date(2026, 6, 1), effective_to=None,
                      source_ref="later-corrected-circular")
    with db.connect() as con:
        persist(con, record, permissions=_permissions())
        with pytest.raises(SchemeMasterError, match="overlapping effective"):
            persist(con, overlap, permissions=_permissions())


def test_invalid_source_hash_and_naive_timestamps_are_rejected():
    with pytest.raises(SchemeMasterError, match="lowercase SHA-256"):
        _record(source_sha256="NOT-A-HASH")
    with pytest.raises(SchemeMasterError, match="published_at must be timezone-aware"):
        _record(published_at=datetime(2026, 1, 1))


def test_effective_date_uses_indian_local_calendar_date(tmp_path):
    record = _record(
        effective_from=date(2026, 11, 11), effective_to=None,
        published_at=datetime(2026, 11, 10, 18, tzinfo=timezone.utc),
        observed_at=datetime(2026, 11, 10, 18, 15, tzinfo=timezone.utc),
    )
    record.require_permissions(_permissions())
    db = _database(tmp_path)
    with db.connect() as con:
        persist(con, record, permissions=_permissions())
        before_ist_midnight = as_known_on(
            con, "100001", "DIRECT", "GROWTH", "INF000000001",
            decision_at=datetime(2026, 11, 10, 17, tzinfo=timezone.utc),
        )
        after_ist_midnight = as_known_on(
            con, "100001", "DIRECT", "GROWTH", "INF000000001",
            decision_at=datetime(2026, 11, 10, 20, tzinfo=timezone.utc),
        )
        assert before_ist_midnight is None
        assert after_ist_midnight is not None
