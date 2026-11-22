from datetime import datetime, timezone
import pytest

from financial_brain.config import Config
from financial_brain.paper.mf_folio_book import reconcile_legacy_lot_folio
from financial_brain.paper.mf_lot_book import MfPaperBookError
from financial_brain.storage.db import Database


def test_legacy_lot_folio_reconciliation_is_evidence_bound_and_immutable(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    recorded_at = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
    with db.connect() as con:
        con.execute("""INSERT INTO mf_paper_lots (
            account_id, lot_id, scheme_code, plan, option, isin, allotted_on,
            units_original, units_remaining, purchase_nav, subscription_event_id, created_at)
            VALUES ('paper', 'legacy-lot', '123456', 'DIRECT', 'GROWTH', 'INF123456789',
            DATE '2026-01-01', 2, 2, 10, 'legacy-subscription', ?)""", [recorded_at])
        assert con.execute("SELECT folio_id FROM mf_paper_lots").fetchone() == (None,)

        evidence = dict(
            account_id="paper", lot_id="legacy-lot", folio_id="paper-folio-9",
            evidence_ref="owner-provided-rta-statement#holding-1",
            evidence_sha256="a" * 64, reconciled_by="account-owner",
            recorded_at=recorded_at,
        )
        first = reconcile_legacy_lot_folio(con, **evidence)
        assert first["status"] == "reconciled"
        assert reconcile_legacy_lot_folio(con, **evidence)["status"] == "already_reconciled"
        assert con.execute("SELECT folio_id FROM mf_paper_lots").fetchone() == (
            "paper-folio-9",
        )
        assert con.execute("""SELECT folio_id, evidence_ref, evidence_sha256,
            reconciled_by FROM mf_paper_folio_reconciliations""").fetchone() == (
            "paper-folio-9", "owner-provided-rta-statement#holding-1",
            "a" * 64, "account-owner",
        )
        with pytest.raises(MfPaperBookError, match="immutable"):
            reconcile_legacy_lot_folio(con, **(evidence | {"folio_id": "other-folio"}))
        con.execute("""INSERT INTO mf_paper_lots (
            account_id, lot_id, folio_id, scheme_code, plan, option, isin, allotted_on,
            units_original, units_remaining, purchase_nav, subscription_event_id, created_at)
            VALUES ('paper', 'already-owned-lot', 'existing-folio', '123456', 'DIRECT',
            'GROWTH', 'INF123456789', DATE '2026-01-01', 1, 1, 10,
            'already-owned-subscription', ?)""", [recorded_at])
        with pytest.raises(MfPaperBookError, match="already has a folio"):
            reconcile_legacy_lot_folio(
                con, **(evidence | {"lot_id": "already-owned-lot", "folio_id": "other-folio",
                                   "evidence_ref": "other-evidence"})
            )


def test_database_migration_does_not_guess_legacy_folio_ownership(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as con:
        con.execute("""INSERT INTO mf_paper_lots (
            account_id, lot_id, scheme_code, plan, option, isin, allotted_on,
            units_original, units_remaining, purchase_nav, subscription_event_id, created_at)
            VALUES ('paper', 'legacy-lot', '123456', 'DIRECT', 'GROWTH', 'INF123456789',
            DATE '2026-01-01', 2, 2, 10, 'legacy-subscription',
            TIMESTAMPTZ '2026-11-22 12:00:00+00')""")
    db.migrate()
    with db.connect() as con:
        assert con.execute("SELECT folio_id FROM mf_paper_lots").fetchone() == (None,)
