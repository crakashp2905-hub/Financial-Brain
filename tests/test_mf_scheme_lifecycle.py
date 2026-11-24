from datetime import date, datetime, timezone

import pytest

from financial_brain.config import Config
from financial_brain.forward_edge.data_permissions import (
    DataPermission, DataUse, PermissionStatus,
)
from financial_brain.ingest.mf_scheme_lifecycle import as_known_on, persist
from financial_brain.providers.mf_scheme_lifecycle import (
    SchemeLifecycle, SchemeLifecycleError, SchemeLifecycleRecord,
)
from financial_brain.storage.db import Database

SOURCE_HASH = "b" * 64
SOURCE_ID = "AMFI"


def _permissions(*uses: DataUse) -> tuple[DataPermission, ...]:
    return tuple(DataPermission(
        source_id=SOURCE_ID, use=use, status=PermissionStatus.USE_ALLOWED,
        effective_from=date(2025, 1, 1), effective_through=None,
        evidence_ref="reviewed-provider-terms", reviewer="data-owner",
    ) for use in uses)


def _record(lifecycle: SchemeLifecycle, effective_from: date, effective_to: date | None,
            observed_at: datetime, *, successor: str | None = None,
            source_hash: str = SOURCE_HASH) -> SchemeLifecycleRecord:
    return SchemeLifecycleRecord(
        scheme_code="100001", plan="DIRECT", option="GROWTH", isin="INF000000001",
        lifecycle=lifecycle, effective_from=effective_from, effective_to=effective_to,
        successor_scheme_code=successor, source_id=SOURCE_ID,
        source_ref=f"amfi-notice-{effective_from.isoformat()}",
        source_sha256=source_hash, published_at=observed_at, observed_at=observed_at,
    )


def test_closed_scheme_remains_resolvable_at_its_old_effective_dates(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    permissions = _permissions(DataUse.RESEARCH, DataUse.RETENTION)
    active = _record(SchemeLifecycle.ACTIVE, date(2025, 1, 1), date(2026, 3, 31),
                     datetime(2025, 1, 2, tzinfo=timezone.utc))
    closed = _record(SchemeLifecycle.WOUND_UP, date(2026, 4, 1), None,
                     datetime(2026, 4, 3, tzinfo=timezone.utc))
    with db.connect() as con:
        persist(con, active, permissions=permissions)
        persist(con, closed, permissions=permissions)
        old = as_known_on(
            con, "100001", "DIRECT", "GROWTH", "INF000000001",
            decision_date=date(2025, 6, 1),
            decision_at=datetime(2025, 6, 1, 12, tzinfo=timezone.utc),
        )
        before_notice = as_known_on(
            con, "100001", "DIRECT", "GROWTH", "INF000000001",
            decision_date=date(2026, 4, 2),
            decision_at=datetime(2026, 4, 2, 12, tzinfo=timezone.utc),
        )
        after_notice = as_known_on(
            con, "100001", "DIRECT", "GROWTH", "INF000000001",
            decision_date=date(2026, 4, 2),
            decision_at=datetime(2026, 4, 4, 12, tzinfo=timezone.utc),
        )
        assert old is not None and old["lifecycle"] == "ACTIVE"
        assert before_notice is None
        assert after_notice is not None and after_notice["lifecycle"] == "WOUND_UP"
        assert con.execute("SELECT count(*) FROM mf_scheme_lifecycle").fetchone()[0] == 2


def test_merge_requires_explicit_successor_identity():
    with pytest.raises(SchemeLifecycleError, match="successor_scheme_code"):
        _record(SchemeLifecycle.MERGED, date(2026, 1, 1), None,
                datetime(2026, 1, 2, tzinfo=timezone.utc))


def test_retention_permission_is_required_before_persist(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    record = _record(SchemeLifecycle.SUSPENDED, date(2026, 1, 1), None,
                     datetime(2026, 1, 2, tzinfo=timezone.utc))
    with db.connect() as con:
        with pytest.raises(SchemeLifecycleError, match="RETENTION"):
            persist(con, record, permissions=_permissions(DataUse.RESEARCH))
        assert con.execute("SELECT count(*) FROM mf_scheme_lifecycle").fetchone()[0] == 0


def test_future_india_local_decision_date_is_rejected(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as con:
        with pytest.raises(ValueError, match="after decision_at"):
            as_known_on(
                con, "100001", "DIRECT", "GROWTH", "INF000000001",
                decision_date=date(2026, 1, 2),
                decision_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
            )
