from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.config import Config
from financial_brain.forward_edge.data_permissions import (
    DataPermission, DataUse, PermissionStatus,
)
from financial_brain.paper.mf_account_ledger import (
    MFPaperLedgerError, ledger_snapshot, post_journal, record_external_cash_flow,
)
from financial_brain.paper.mf_lot_book import create_account
from financial_brain.storage.db import Database

NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
HASH = "a" * 64


@pytest.fixture
def connection(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as con:
        yield con


def test_opening_and_external_cash_flows_are_balanced_idempotent_and_reconcile(connection):
    con = connection
    create_account(con, "ledger-account", 100, now=NOW)
    contribution = record_external_cash_flow(
        con, account_id="ledger-account", event_id="deposit-1", amount_inr=50,
        direction="CONTRIBUTION", effective_at=NOW, recorded_at=NOW,
        basis="SIMULATED_ASSUMPTION",
    )
    assert contribution["status"] == "recorded"
    retry = record_external_cash_flow(
        con, account_id="ledger-account", event_id="deposit-1", amount_inr=50,
        direction="CONTRIBUTION", effective_at=NOW, recorded_at=NOW,
        basis="SIMULATED_ASSUMPTION",
    )
    assert retry["status"] == "already_recorded"
    record_external_cash_flow(
        con, account_id="ledger-account", event_id="withdrawal-1", amount_inr=25,
        direction="WITHDRAWAL", effective_at=NOW, recorded_at=NOW,
        basis="SIMULATED_ASSUMPTION",
    )

    snapshot = ledger_snapshot(con, account_id="ledger-account")
    assert snapshot["cash_inr"] == Decimal("125")
    assert snapshot["journal_cash_inr"] == Decimal("125")
    assert snapshot["cash_difference_inr"] == 0
    assert snapshot["is_reconciled"] is True
    totals = con.execute("""SELECT SUM(debit_inr), SUM(credit_inr)
        FROM mf_paper_ledger_entries WHERE account_id='ledger-account'""").fetchone()
    assert Decimal(totals[0]) == Decimal(totals[1]) == Decimal("175")


def test_external_withdrawal_cannot_overdraw_and_rollback_is_clean(connection):
    create_account(connection, "ledger-account", 10, now=NOW)
    with pytest.raises(MFPaperLedgerError, match="exceeds available cash"):
        record_external_cash_flow(
            connection, account_id="ledger-account", event_id="too-large", amount_inr=11,
            direction="WITHDRAWAL", effective_at=NOW, recorded_at=NOW,
            basis="SIMULATED_ASSUMPTION",
        )
    assert ledger_snapshot(connection, account_id="ledger-account")["cash_inr"] == 10
    assert connection.execute("""SELECT COUNT(*) FROM mf_paper_external_cash_events
        WHERE account_id='ledger-account'""").fetchone()[0] == 0


def test_observed_external_flow_requires_effective_permission_and_provenance(connection):
    create_account(connection, "ledger-account", 10, now=NOW)
    args = dict(
        account_id="ledger-account", event_id="bank-deposit", amount_inr=7,
        direction="CONTRIBUTION", effective_at=NOW, recorded_at=NOW,
        basis="OBSERVED_EVIDENCE", source_id="BANK", source_ref="statement:line-7",
        source_sha256=HASH,
    )
    with pytest.raises(MFPaperLedgerError, match="lacks PAPER_SIMULATION permission"):
        record_external_cash_flow(connection, **args)
    permission = DataPermission(
        source_id="BANK", use=DataUse.PAPER_SIMULATION,
        status=PermissionStatus.USE_ALLOWED, effective_from=date(2026, 11, 1),
        effective_through=None, evidence_ref="reviewed-bank-terms", reviewer="owner",
    )
    record_external_cash_flow(connection, **args, permissions=(permission,))
    rows = connection.execute("""SELECT DISTINCT basis, source_id, source_ref,
        source_sha256 FROM mf_paper_ledger_entries WHERE account_id='ledger-account'
          AND transaction_id='external:bank-deposit'""").fetchall()
    assert rows == [("OBSERVED_EVIDENCE", "BANK", "statement:line-7", HASH)]


def test_journal_rejects_unbalanced_entries(connection):
    create_account(connection, "ledger-account", 0, now=NOW)
    with pytest.raises(MFPaperLedgerError, match="do not balance"):
        post_journal(
            connection, account_id="ledger-account", transaction_id="unbalanced",
            effective_at=NOW, recorded_at=NOW,
            entries=(("CASH", 10, 0), ("OWNER_EQUITY", 0, 9)),
        )
