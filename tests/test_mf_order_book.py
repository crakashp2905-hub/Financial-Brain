from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.config import Config
from financial_brain.forward_edge.data_permissions import (
    DataPermission, DataUse, PermissionStatus,
)
from financial_brain.kernel.mf_order import (
    MFOrder, MFOrderAcknowledgement, MFOrderEvidenceBasis, MFOrderKind, MFOrderState,
)
from financial_brain.kernel.fund_transactions import SchemeTransactionCalendar
from financial_brain.kernel.trading_clock import BusinessCalendar, CalendarDomain
from financial_brain.paper.mf_order_book import (
    MFPaperOrderBookError, order_snapshot, record_response, submit_order,
)
from financial_brain.storage.db import Database


REQUESTED_AT = datetime(2026, 11, 23, 5, tzinfo=timezone.utc)


@pytest.fixture
def connection(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as con:
        con.execute("INSERT INTO mf_paper_accounts VALUES (?, ?, ?)",
                    ["paper-account-1", Decimal(0), REQUESTED_AT])
        yield con


def _order(**changes):
    values = {
        "order_id": "paper-mf-order-1", "account_id": "paper-account-1",
        "folio_id": "SYNTHETIC-FOLIO-1", "scheme_code": "123456",
        "plan": "DIRECT", "option": "GROWTH", "transaction_type": MFOrderKind.PURCHASE,
        "requested_at": REQUESTED_AT, "amount_inr": Decimal("10000"),
    }
    return MFOrder(**(values | changes))


def _permission():
    return DataPermission(
        source_id="PLATFORM", use=DataUse.PAPER_SIMULATION,
        status=PermissionStatus.USE_ALLOWED, effective_from=date(2026, 11, 23),
        effective_through=None, evidence_ref="paper-use-approval", reviewer="owner",
    )


def _calendar():
    scheme = BusinessCalendar(
        calendar_id="123456", domain=CalendarDomain.SCHEME,
        coverage_start=date(2026, 11, 23), source_as_of=date(2026, 11, 26),
        open_days=frozenset(date(2026, 11, day) for day in (23, 24, 25, 26)),
        source_ref="scheme-calendar", source_sha256="e" * 64,
        recorded_at=REQUESTED_AT,
    )
    return SchemeTransactionCalendar({"123456": scheme}, ())


def _submit(con, order):
    return submit_order(con, order, valid_business_days=2, calendar=_calendar(),
                        now=REQUESTED_AT + timedelta(seconds=2))


def _response(state, *, basis=MFOrderEvidenceBasis.SIMULATED_ASSUMPTION, **changes):
    at = REQUESTED_AT + timedelta(minutes=1)
    values = {
        "order_id": "paper-mf-order-1", "state": state,
        "effective_at": at, "received_at": at + timedelta(seconds=1), "basis": basis,
    }
    if basis is MFOrderEvidenceBasis.OBSERVED_EVIDENCE:
        values.update(source_id="PLATFORM", source_ref="receipt-1", source_sha256="a" * 64)
    return MFOrderAcknowledgement(**(values | changes))


def test_request_persists_immutable_identity_and_is_idempotent(connection):
    order = _order()
    first = _submit(connection, order)
    again = _submit(connection, order)
    assert first["status"] == "submitted"
    assert again["status"] == "already_submitted"
    snapshot = order_snapshot(connection, order_id=order.order_id)
    assert snapshot["current_state"] == "REQUESTED"
    assert len(snapshot["transitions"]) == 1
    with pytest.raises(MFPaperOrderBookError, match="reused"):
        submit_order(connection, order, valid_business_days=3, calendar=_calendar(),
                     now=REQUESTED_AT + timedelta(seconds=3))


def test_ack_then_simulated_acceptance_keeps_official_claim_false_and_replays_idempotently(connection):
    order = _order()
    _submit(connection, order)
    ack = _response(MFOrderState.ACKNOWLEDGED)
    assert record_response(connection, ack, now=ack.received_at)["status"] == "recorded"
    accepted = _response(MFOrderState.ACCEPTED,
                         received_at=REQUESTED_AT + timedelta(minutes=2))
    result = record_response(connection, accepted, now=accepted.received_at)
    assert not result["is_official_acceptance"]
    assert record_response(connection, accepted, now=accepted.received_at)["status"] == "already_recorded"
    assert order_snapshot(connection, order_id=order.order_id)["current_state"] == "ACCEPTED"


def test_observed_acceptance_requires_effective_permission_and_preserves_receipt(connection):
    order = _order()
    _submit(connection, order)
    response = _response(MFOrderState.ACCEPTED, basis=MFOrderEvidenceBasis.OBSERVED_EVIDENCE)
    with pytest.raises(MFPaperOrderBookError, match="lacks PAPER_SIMULATION"):
        record_response(connection, response, now=response.received_at)
    result = record_response(connection, response, permissions=(_permission(),),
                             now=response.received_at)
    assert result["is_official_acceptance"]
    transition = order_snapshot(connection, order_id=order.order_id)["transitions"][-1]
    assert transition[2] == response.effective_at
    assert transition[3] == response.received_at
    assert transition[7] == "a" * 64


def test_rejection_is_terminal_and_must_include_reason(connection):
    order = _order()
    _submit(connection, order)
    rejected = _response(MFOrderState.REJECTED, reason="invalid folio")
    record_response(connection, rejected, now=rejected.received_at)
    with pytest.raises(MFPaperOrderBookError, match="terminal"):
        record_response(connection, _response(MFOrderState.ACCEPTED),
                        now=REQUESTED_AT + timedelta(minutes=3))
    assert order_snapshot(connection, order_id=order.order_id)["current_state"] == "REJECTED"


def test_expired_platform_order_cannot_be_accepted(connection):
    order = _order()
    _submit(connection, order)
    expired_at = datetime(2026, 11, 25, 1, tzinfo=timezone.utc)
    expired = MFOrderAcknowledgement(
        order_id=order.order_id, state=MFOrderState.ACCEPTED,
        effective_at=expired_at, received_at=expired_at + timedelta(seconds=1),
        basis=MFOrderEvidenceBasis.SIMULATED_ASSUMPTION,
    )
    with pytest.raises(MFPaperOrderBookError, match="outside platform business-day validity"):
        record_response(connection, expired, now=expired.received_at)
    assert order_snapshot(connection, order_id=order.order_id)["current_state"] == "REQUESTED"


def test_unit_redemption_acceptance_requires_and_links_matching_folio_reservation(connection):
    from financial_brain.paper.mf_redemption_settlement import request_redemption

    order = _order(transaction_type=MFOrderKind.REDEMPTION,
                   amount_inr=None, units=Decimal("2"))
    _submit(connection, order)
    accepted = _response(MFOrderState.ACCEPTED)
    with pytest.raises(MFPaperOrderBookError, match="requires one matching folio reservation"):
        record_response(connection, accepted, now=accepted.received_at)
    assert order_snapshot(connection, order_id=order.order_id)["current_state"] == "REQUESTED"

    connection.execute("""INSERT INTO mf_paper_lots (
        account_id, lot_id, folio_id, scheme_code, plan, option, isin, allotted_on,
        units_original, units_remaining, purchase_nav, subscription_event_id, created_at)
        VALUES ('paper-account-1', 'owned-lot', 'SYNTHETIC-FOLIO-1', '123456',
        'DIRECT', 'GROWTH', 'INF123456789', DATE '2026-11-20', 3, 3, 10,
        'owned-subscription', ?)""", [REQUESTED_AT])
    request_redemption(
        connection, account_id=order.account_id, folio_id=order.folio_id,
        settlement_id="redemption-reservation", scheme_code=order.scheme_code,
        plan=order.plan, option=order.option, isin="INF123456789",
        requested_units=order.units, requested_on=REQUESTED_AT.date(),
        requested_at=order.requested_at, now=REQUESTED_AT + timedelta(seconds=1),
    )
    assert record_response(connection, accepted, now=accepted.received_at)["status"] == "recorded"
    assert connection.execute("""SELECT order_id FROM mf_paper_redemption_settlements
        WHERE account_id=? AND settlement_id=?""",
        [order.account_id, "redemption-reservation"]).fetchone() == (order.order_id,)
