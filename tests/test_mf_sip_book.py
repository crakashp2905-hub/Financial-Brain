from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.data_permissions import (
    DataPermission, DataUse, PermissionStatus,
)
from financial_brain.ingest.mf_scheme_terms import persist as persist_scheme_terms
from financial_brain.config import Config
from financial_brain.kernel.mf_sip import (
    MFSIPInstallment, MFSIPSeries, MFSIPState,
)
from financial_brain.paper.mf_sip_book import (
    MFSIPBookError, advance_installment, apply_sip_allotment, create_sip_series,
    installment_snapshot,
)
from financial_brain.paper.mf_account_ledger import ledger_snapshot, post_journal
from financial_brain.providers.mf_scheme_terms import SchemeCommercialTerms
from financial_brain.storage.db import Database


NOW = datetime(2026, 11, 23, 5, tzinfo=timezone.utc)


@pytest.fixture
def connection(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as con:
        con.execute("INSERT INTO mf_paper_accounts VALUES (?, ?, ?)",
                    ["account-1", Decimal(0), NOW])
        yield con


def _item(month, **changes):
    values = {
        "installment_id": f"sip-1-2026-{month:02d}", "plan_id": "sip-1",
        "account_id": "account-1", "folio_id": "SYNTHETIC-FOLIO",
        "scheme_code": "123456", "plan": "DIRECT", "option": "GROWTH",
        "scheduled_for": date(2026, month, 23), "amount_inr": Decimal("5000"),
    }
    return MFSIPInstallment(**(values | changes))


def test_series_schedule_is_durable_idempotent_and_installments_are_independent(connection):
    series = MFSIPSeries("sip-1", (_item(10), _item(11)))
    result = create_sip_series(connection, series, now=NOW)
    assert result["status"] == "created"
    assert create_sip_series(connection, series, now=NOW)["status"] == "already_created"
    initiated = _item(10, state=MFSIPState.PAYMENT_INITIATED,
                      payment_initiated_at=NOW + timedelta(minutes=1))
    assert advance_installment(connection, initiated, now=initiated.payment_initiated_at)["status"] == "advanced"
    assert advance_installment(connection, initiated, now=initiated.payment_initiated_at)["status"] == "already_applied"
    first = installment_snapshot(connection, installment_id=initiated.installment_id)
    second = installment_snapshot(connection, installment_id="sip-1-2026-11")
    assert first["installment"].state is MFSIPState.PAYMENT_INITIATED
    assert len(first["transitions"]) == 2
    assert second["installment"].state is MFSIPState.SCHEDULED
    assert len(second["transitions"]) == 1


def test_persisted_missed_installment_cannot_be_retried(connection):
    scheduled = _item(10)
    series = MFSIPSeries("sip-1", (scheduled,))
    create_sip_series(connection, series, now=NOW)
    missed = _item(10, state=MFSIPState.MISSED,
                   missed_at=NOW + timedelta(minutes=1), miss_reason="mandate failed")
    advance_installment(connection, missed, now=missed.missed_at)
    retry = _item(10, state=MFSIPState.PAYMENT_INITIATED,
                  payment_initiated_at=NOW + timedelta(minutes=2))
    with pytest.raises(MFSIPBookError, match="illegal SIP transition MISSED"):
        advance_installment(connection, retry, now=retry.payment_initiated_at)
    assert installment_snapshot(connection, installment_id=scheduled.installment_id)[
        "installment"].state is MFSIPState.MISSED


def test_plan_id_cannot_be_reused_for_different_schedule(connection):
    create_sip_series(connection, MFSIPSeries("sip-1", (_item(10),)), now=NOW)
    changed = _item(10, amount_inr=Decimal("6000"))
    with pytest.raises(MFSIPBookError, match="different schedule"):
        create_sip_series(connection, MFSIPSeries("sip-1", (changed,)), now=NOW)


def test_installment_transition_requires_account_and_causal_recording_time(connection):
    missing_account = _item(10, account_id="absent-account")
    with pytest.raises(MFSIPBookError, match="paper account does not exist"):
        create_sip_series(connection, MFSIPSeries("sip-1", (missing_account,)), now=NOW)
    scheduled = _item(10)
    create_sip_series(connection, MFSIPSeries("sip-1", (scheduled,)), now=NOW)
    initiated = _item(10, state=MFSIPState.PAYMENT_INITIATED,
                      payment_initiated_at=NOW + timedelta(minutes=2))
    with pytest.raises(MFSIPBookError, match="recorded before event time"):
        advance_installment(connection, initiated, now=NOW + timedelta(minutes=1))


def test_missed_installment_survives_database_reopen(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as con:
        con.execute("INSERT INTO mf_paper_accounts VALUES (?, ?, ?)",
                    ["account-1", Decimal(0), NOW])
        scheduled = _item(10)
        create_sip_series(con, MFSIPSeries("sip-1", (scheduled,)), now=NOW)
        missed = _item(10, state=MFSIPState.MISSED,
                       missed_at=NOW + timedelta(minutes=1), miss_reason="mandate failed")
        advance_installment(con, missed, now=missed.missed_at)
    with db.connect() as con:
        snapshot = installment_snapshot(con, installment_id="sip-1-2026-10")
        assert snapshot["installment"].state is MFSIPState.MISSED
        assert len(snapshot["transitions"]) == 2


def test_sip_payment_reserve_and_allotment_reconcile_atomically(connection, monkeypatch):
    import financial_brain.paper.mf_sip_book as sip_book

    con = connection
    opening_at = NOW - timedelta(minutes=1)
    con.execute("UPDATE mf_paper_accounts SET cash_inr=? WHERE account_id=?",
                [Decimal("5000"), "account-1"])
    post_journal(
        con, account_id="account-1", transaction_id="opening:sip",
        effective_at=opening_at, recorded_at=opening_at,
        entries=(("CASH", 5000, 0), ("OWNER_EQUITY", 0, 5000)),
    )
    installment = _item(11, scheduled_for=date(2026, 11, 23))
    create_sip_series(con, MFSIPSeries("sip-1", (installment,)), now=NOW)
    initiated_at = NOW + timedelta(minutes=1)
    initiated = _item(11, scheduled_for=date(2026, 11, 23),
                      state=MFSIPState.PAYMENT_INITIATED,
                      payment_initiated_at=initiated_at)
    advance_installment(con, initiated, now=initiated_at)
    payment_permissions = (DataPermission(
        source_id="BANK_FEED", use=DataUse.PAPER_SIMULATION,
        status=PermissionStatus.USE_ALLOWED,
        effective_from=date(2026, 11, 1), effective_through=None,
        evidence_ref="bank-feed-use-review", reviewer="account-owner",
    ),)
    paid_at = NOW + timedelta(minutes=2)
    paid = _item(11, scheduled_for=date(2026, 11, 23),
                 state=MFSIPState.FUNDS_AVAILABLE,
                 payment_initiated_at=initiated_at, funds_available_at=paid_at)
    payment_kwargs = dict(
        payment_basis="OBSERVED_EVIDENCE", payment_source_id="BANK_FEED",
        payment_source_ref="debit-receipt-2026-11-23-001",
        payment_source_sha256="c" * 64, permissions=payment_permissions,
    )
    advance_installment(con, paid, now=paid_at, **payment_kwargs)
    advance_installment(con, paid, now=paid_at, **payment_kwargs)
    with pytest.raises(MFSIPBookError, match="changed payment or allotment evidence"):
        advance_installment(con, paid, now=paid_at,
                            **(payment_kwargs | {"payment_source_ref": "different-receipt"}))
    snapshot = ledger_snapshot(con, account_id="account-1")
    assert snapshot["is_reconciled"] and snapshot["journal_balanced"]
    assert snapshot["cash_inr"] == 0
    assert snapshot["balances_inr"]["SIP_PAYMENT_PENDING"] == Decimal("5000")
    payment_row = con.execute("""SELECT basis, source_id, source_ref, source_sha256
        FROM mf_paper_ledger_entries WHERE account_id=? AND transaction_id=?
        AND line_no=1""", ["account-1", f"sip-payment:{installment.installment_id}"]
    ).fetchone()
    assert payment_row == (
        "OBSERVED_EVIDENCE", "BANK_FEED", "debit-receipt-2026-11-23-001", "c" * 64,
    )

    permissions = tuple(DataPermission(
        source_id=source_id, use=use, status=PermissionStatus.USE_ALLOWED,
        effective_from=date(2026, 11, 1), effective_through=None,
        evidence_ref="reviewed-paper-source", reviewer="account-owner",
    ) for source_id in ("AMC", "AMFI") for use in (
        DataUse.RESEARCH, DataUse.RETENTION, DataUse.PAPER_SIMULATION,
    ))
    terms = SchemeCommercialTerms(
        scheme_code=installment.scheme_code, plan=installment.plan,
        option=installment.option, isin="INF123456789",
        effective_from=date(2026, 11, 1), effective_to=None,
        ter_percent=None, aum_inr=None, exit_loads=(),
        minimum_initial_inr=Decimal("0"), minimum_additional_inr=Decimal("0"),
        minimum_redemption_inr=Decimal("0"), exit_restrictions=(),
        source_id="AMC", source_ref="reviewed-sip-terms",
        source_sha256="a" * 64,
        published_at=NOW - timedelta(hours=1), observed_at=NOW - timedelta(minutes=30),
    )
    persist_scheme_terms(con, terms, permissions=permissions)
    nav_id = "sip-nav-2026-11-23"
    nav_hash = "b" * 64
    nav_available_at = NOW + timedelta(minutes=10)
    decision_at = NOW + timedelta(minutes=20)
    con.execute("""INSERT INTO mf_nav_observations (
        observation_id, scheme_code, isin_growth, isin_reinvest, scheme_name,
        fund_house, scheme_type, plan, option, nav, nav_date, lake_key,
        row_number, row_sha256, source_published_at, observed_at, ingested_at)
        VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL, ?, ?)""",
        [nav_id, installment.scheme_code, terms.isin, "Test SIP Scheme", "AMC",
         "EQUITY", installment.plan, installment.option, 10.0,
         date(2026, 11, 23), "sip-nav-lake", nav_hash,
         nav_available_at, nav_available_at])
    allotted_at = NOW + timedelta(minutes=25)
    kwargs = dict(
        installment_id=installment.installment_id, nav_date=date(2026, 11, 23),
        nav_per_unit=Decimal("10"), nav_observation_id=nav_id, terms=terms,
        permissions=permissions, decision_at=decision_at,
        allotted_at=allotted_at, now=allotted_at + timedelta(seconds=1),
    )
    original_advance = sip_book.advance_installment

    def fail_before_sip_transition(*args, **kwargs):
        raise RuntimeError("simulated failure before SIP state write")

    monkeypatch.setattr(sip_book, "advance_installment", fail_before_sip_transition)
    with pytest.raises(RuntimeError, match="before SIP state write"):
        apply_sip_allotment(con, **kwargs)
    assert installment_snapshot(con, installment_id=installment.installment_id)[
        "installment"].state is MFSIPState.FUNDS_AVAILABLE
    assert con.execute("SELECT COUNT(*) FROM mf_paper_lots WHERE account_id=?",
                       ["account-1"]).fetchone()[0] == 0
    assert ledger_snapshot(con, account_id="account-1")["balances_inr"][
        "SIP_PAYMENT_PENDING"] == Decimal("5000")

    monkeypatch.setattr(sip_book, "advance_installment", original_advance)
    result = apply_sip_allotment(con, **kwargs)
    assert result["state"] == MFSIPState.ALLOTTED.value
    assert result["units"] == Decimal("500")
    assert apply_sip_allotment(con, **kwargs)["status"] == "already_applied"
    snapshot = installment_snapshot(con, installment_id=installment.installment_id)
    assert snapshot["installment"].state is MFSIPState.ALLOTTED
    assert snapshot["installment"].nav_published_at is None
    lot = con.execute("""SELECT units_remaining, purchase_nav FROM mf_paper_lots
        WHERE account_id=? AND lot_id=?""",
        ["account-1", f"sip-lot:{installment.installment_id}"]).fetchone()
    assert (Decimal(lot[0]), Decimal(lot[1])) == (Decimal("500"), Decimal("10"))
    final_ledger = ledger_snapshot(con, account_id="account-1")
    assert final_ledger["is_reconciled"] and final_ledger["journal_balanced"]
    assert final_ledger["cash_inr"] == Decimal(0)
    assert final_ledger["balances_inr"]["SIP_PAYMENT_PENDING"] == Decimal(0)
    assert final_ledger["balances_inr"]["MF_INVESTMENTS"] == Decimal("5000")
