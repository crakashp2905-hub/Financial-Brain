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
    first = submit_order(connection, order, valid_through=date(2026, 11, 24),
                         now=REQUESTED_AT + timedelta(seconds=2))
    again = submit_order(connection, order, valid_through=date(2026, 11, 24),
                          now=REQUESTED_AT + timedelta(seconds=3))
    assert first["status"] == "submitted"
    assert again["status"] == "already_submitted"
    snapshot = order_snapshot(connection, order_id=order.order_id)
    assert snapshot["current_state"] == "REQUESTED"
    assert len(snapshot["transitions"]) == 1
    with pytest.raises(MFPaperOrderBookError, match="reused"):
        submit_order(connection, order, valid_through=date(2026, 11, 25),
                     now=REQUESTED_AT + timedelta(seconds=3))


def test_ack_then_simulated_acceptance_keeps_official_claim_false_and_replays_idempotently(connection):
    order = _order()
    submit_order(connection, order, valid_through=date(2026, 11, 24),
                 now=REQUESTED_AT + timedelta(seconds=2))
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
    submit_order(connection, order, valid_through=date(2026, 11, 24),
                 now=REQUESTED_AT + timedelta(seconds=2))
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
    submit_order(connection, order, valid_through=date(2026, 11, 24),
                 now=REQUESTED_AT + timedelta(seconds=2))
    rejected = _response(MFOrderState.REJECTED, reason="invalid folio")
    record_response(connection, rejected, now=rejected.received_at)
    with pytest.raises(MFPaperOrderBookError, match="terminal"):
        record_response(connection, _response(MFOrderState.ACCEPTED),
                        now=REQUESTED_AT + timedelta(minutes=3))
    assert order_snapshot(connection, order_id=order.order_id)["current_state"] == "REJECTED"
