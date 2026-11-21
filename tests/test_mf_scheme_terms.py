from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.config import Config
from financial_brain.forward_edge.data_permissions import (
    DataPermission, DataUse, PermissionStatus,
)
from financial_brain.ingest.mf_scheme_terms import as_known_on, persist
from financial_brain.providers.mf_scheme_terms import (
    ExitLoadBand, ExitRestriction, SchemeCommercialTerms, SchemeTermsError,
)
from financial_brain.storage.db import Database

HASH = "c" * 64
SOURCE = "AMC"


def _permissions():
    return tuple(DataPermission(
        source_id=SOURCE, use=use, status=PermissionStatus.USE_ALLOWED,
        effective_from=date(2025, 1, 1), effective_through=None,
        evidence_ref="reviewed-terms", reviewer="data-owner",
    ) for use in (DataUse.RESEARCH, DataUse.RETENTION))


def _terms(**changes):
    values = {
        "scheme_code": "100001", "plan": "DIRECT", "option": "GROWTH",
        "isin": "INF000000001", "effective_from": date(2026, 1, 1),
        "effective_to": None, "ter_percent": Decimal("0.42"),
        "aum_inr": Decimal("123456789.125"),
        "exit_loads": (ExitLoadBand(0, Decimal("1.00")),
                       ExitLoadBand(365, Decimal("0.00"))),
        "minimum_initial_inr": Decimal("5000.00"),
        "minimum_additional_inr": Decimal("1000.00"),
        "exit_restrictions": (ExitRestriction.ELSS_LOCK_IN,),
        "source_id": SOURCE, "source_ref": "sid-2026-01",
        "source_sha256": HASH,
        "published_at": datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
        "observed_at": datetime(2026, 1, 2, 8, tzinfo=timezone.utc),
    }
    values.update(changes)
    return SchemeCommercialTerms(**values)


def _db(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    return db


def test_source_terms_round_trip_exact_values_and_known_at_visibility(tmp_path):
    db = _db(tmp_path)
    record = _terms()
    with db.connect() as con:
        record_id = persist(con, record, permissions=_permissions())
        assert persist(con, record, permissions=_permissions()) == record_id
        before_observed = as_known_on(
            con, "100001", "DIRECT", "GROWTH", "INF000000001",
            decision_date=date(2026, 1, 2),
            decision_at=datetime(2026, 1, 2, 7, tzinfo=timezone.utc),
        )
        after_observed = as_known_on(
            con, "100001", "DIRECT", "GROWTH", "INF000000001",
            decision_date=date(2026, 1, 2),
            decision_at=datetime(2026, 1, 2, 9, tzinfo=timezone.utc),
        )
        assert before_observed is None
        assert after_observed is not None
        assert after_observed["ter_percent"] == Decimal("0.42")
        assert after_observed["aum_inr"] == Decimal("123456789.125")
        assert after_observed["exit_loads_json"] == [
            {"days_from_purchase": 0, "rate_percent": "1.00"},
            {"days_from_purchase": 365, "rate_percent": "0.00"},
        ]
        assert after_observed["exit_restrictions_json"] == ["ELSS_LOCK_IN"]
        assert con.execute("SELECT count(*) FROM mf_scheme_commercial_terms").fetchone()[0] == 1


def test_unknown_and_explicitly_empty_schedules_remain_distinct(tmp_path):
    db = _db(tmp_path)
    unknown = _terms(ter_percent=None, aum_inr=None, exit_loads=None,
                     exit_restrictions=None)
    explicitly_clear = _terms(
        source_sha256="d" * 64, exit_loads=(), exit_restrictions=(),
    )
    with db.connect() as con:
        persist(con, unknown, permissions=_permissions())
        persist(con, explicitly_clear, permissions=_permissions())
        # Same effective interval, different published version: as-known is selected
        # by source publication and observation clocks; equal clocks are ambiguous.
        with pytest.raises(SchemeTermsError, match="ambiguous"):
            as_known_on(
                con, "100001", "DIRECT", "GROWTH", "INF000000001",
                decision_date=date(2026, 1, 2),
                decision_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            )


def test_exit_load_thresholds_must_be_strictly_increasing():
    with pytest.raises(SchemeTermsError, match="strictly increasing"):
        _terms(exit_loads=(ExitLoadBand(0, Decimal("1")),
                           ExitLoadBand(0, Decimal("0"))))


def test_missing_retention_right_blocks_storage(tmp_path):
    db = _db(tmp_path)
    with db.connect() as con:
        with pytest.raises(SchemeTermsError, match="RETENTION"):
            persist(con, _terms(), permissions=(_permissions()[0],))
        assert con.execute("SELECT count(*) FROM mf_scheme_commercial_terms").fetchone()[0] == 0
