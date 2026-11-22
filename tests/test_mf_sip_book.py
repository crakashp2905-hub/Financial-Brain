from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.config import Config
from financial_brain.kernel.mf_sip import (
    MFSIPInstallment, MFSIPSeries, MFSIPState,
)
from financial_brain.paper.mf_sip_book import (
    MFSIPBookError, advance_installment, create_sip_series, installment_snapshot,
)
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
