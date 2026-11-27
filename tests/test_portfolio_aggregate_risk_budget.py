from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import sqlite3

from financial_brain.portfolio.aggregate_risk_budget import (
    AggregateRiskDisposition,
    AggregateRiskPolicy,
    DeskRiskEstimate,
    evaluate_aggregate_risk_budget,
)
from financial_brain.portfolio.canonical_ledger import (
    AssetClass,
    CanonicalPortfolioLedger,
    EventKind,
    PortfolioEvent,
)
from financial_brain.forward_edge.order_journal import OrderEvent, OrderJournal


AS_OF = datetime(2026, 11, 26, 15, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
ACCOUNT = "paper-main"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _event(event_id, kind, *, sleeve="swing", occurred_at=None, **kwargs):
    return PortfolioEvent(
        account_id=ACCOUNT, sleeve_id=sleeve, source_system="test-fixture",
        source_event_id=event_id, source_sha256=_hash("source:" + event_id), event_kind=kind,
        occurred_at=occurred_at or AS_OF.isoformat(), **kwargs,
    )


def _ledger(path, *, opening=True, mark_age_seconds=20, unresolved=False):
    ledger = CanonicalPortfolioLedger(path)
    events = []
    if opening:
        events.append(_event("cash", EventKind.CASH_FLOW, cash_delta="200000",
                             metadata={"cash_flow_type": "OPENING_BALANCE"},
                             occurred_at=(AS_OF - timedelta(hours=6)).isoformat()))
    else:
        events.append(_event("cash", EventKind.CASH_FLOW, cash_delta="200000",
                             occurred_at=(AS_OF - timedelta(hours=6)).isoformat()))
    events.extend([
        _event("equity-buy", EventKind.FILL, asset_class=AssetClass.EQUITY,
               instrument_id="NSE:INFY", side="BUY", quantity="10", price="100",
               occurred_at=(AS_OF - timedelta(hours=5)).isoformat()),
        _event("equity-mark", EventKind.MARK, sleeve="market-data", asset_class=AssetClass.EQUITY,
               instrument_id="NSE:INFY", price="110",
               occurred_at=(AS_OF - timedelta(seconds=mark_age_seconds)).isoformat()),
    ])
    if unresolved:
        events.append(_event("legacy", EventKind.UNRESOLVED_POSITION, asset_class=AssetClass.ETF,
                             instrument_id="NSE:OLD", quantity="1",
                             occurred_at=(AS_OF - timedelta(hours=1)).isoformat()))
    ledger.import_events(events)
    return ledger


def _policy(**overrides):
    values = {
        "account_id": ACCOUNT,
        "policy_version": "v1",
        "frozen_at": AS_OF - timedelta(hours=1),
        "maximum_loss_risk_inr": Decimal("5000"),
        "active_desks": ("swing", "options"),
        "maximum_mark_age_seconds": 60,
        "source_sha256": _hash("policy"),
    }
    values.update(overrides)
    return AggregateRiskPolicy(**values)


def _risks(current_swing="1000", add_swing="500", current_options="2000", add_options="1000"):
    return (
        DeskRiskEstimate("swing", AS_OF, Decimal(current_swing), Decimal(add_swing), _hash("swing-risk")),
        DeskRiskEstimate("options", AS_OF, Decimal(current_options), Decimal(add_options), _hash("options-risk")),
    )


def _evaluate(*, ledger, policy, as_of=AS_OF, desk_risks=None, order_journal=None):
    journal = order_journal or OrderJournal(connection=sqlite3.connect(":memory:"))
    return evaluate_aggregate_risk_budget(
        ledger=ledger, order_journal=journal, policy=policy, as_of=as_of,
        desk_risks=_risks() if desk_risks is None else desk_risks,
    )


def test_shared_account_loss_budget_sums_all_desks_and_binds_ledger_evidence(tmp_path):
    ledger = _ledger(tmp_path / "portfolio.sqlite")
    result = _evaluate(ledger=ledger, policy=_policy())
    assert result.disposition is AggregateRiskDisposition.PERMITTED
    assert result.current_loss_risk_inr == Decimal("3000")
    assert result.projected_loss_risk_inr == Decimal("4500")
    assert result.remaining_loss_risk_inr == Decimal("500")
    assert result.ledger_events_sha256 and result.ledger_snapshot_sha256
    assert result.order_audit_sha256 and result.unresolved_order_ids == ()
    assert result.order_permitted
    assert len(result.decision_sha256) == 64


def test_cap_excess_denies_across_desks_and_risk_evidence_gaps_block(tmp_path):
    ledger = _ledger(tmp_path / "portfolio.sqlite")
    over = _evaluate(ledger=ledger, policy=_policy(maximum_loss_risk_inr=Decimal("4000")))
    assert over.disposition is AggregateRiskDisposition.DENIED_RISK_BUDGET
    assert not over.order_permitted

    missing_desk = _evaluate(ledger=ledger, policy=_policy(), desk_risks=_risks()[:1])
    assert missing_desk.disposition is AggregateRiskDisposition.BLOCKED_RISK_EVIDENCE
    assert not missing_desk.order_permitted


def test_unresolved_missing_balance_stale_marks_and_payload_tamper_fail_closed(tmp_path):
    unresolved_ledger = _ledger(tmp_path / "unresolved.sqlite", unresolved=True)
    unresolved = _evaluate(ledger=unresolved_ledger,
                           policy=_policy())
    assert unresolved.disposition is AggregateRiskDisposition.BLOCKED_LEDGER_UNCERTAIN
    assert unresolved.unresolved_position_ids == ("legacy",)
    assert "UNRESOLVED_POSITIONS" in unresolved.reason
    assert not unresolved.order_permitted
    assert unresolved.decision_sha256 == _evaluate(
        ledger=unresolved_ledger, policy=_policy()).decision_sha256

    no_opening = _evaluate(ledger=_ledger(tmp_path / "no-opening.sqlite", opening=False),
                           policy=_policy())
    assert no_opening.disposition is AggregateRiskDisposition.BLOCKED_LEDGER_UNCERTAIN

    stale = _evaluate(ledger=_ledger(tmp_path / "stale.sqlite", mark_age_seconds=600),
                      policy=_policy())
    assert stale.disposition is AggregateRiskDisposition.BLOCKED_LEDGER_UNCERTAIN

    tampered_path = tmp_path / "tampered.sqlite"
    tampered_ledger = _ledger(tampered_path)
    with sqlite3.connect(tampered_path) as connection:
        row = connection.execute(
            "SELECT event_json FROM portfolio_events WHERE source_event_id='cash'"
        ).fetchone()
        connection.execute(
            "UPDATE portfolio_events SET event_json=? WHERE source_event_id='cash'",
            (row[0].replace("200000", "210000"),),
        )
    tampered = _evaluate(ledger=tampered_ledger, policy=_policy())
    assert tampered.disposition is AggregateRiskDisposition.BLOCKED_LEDGER_UNCERTAIN
    assert "LEDGER_READ_FAILED" in tampered.reason


def test_future_or_mismatched_as_of_risk_estimate_blocks(tmp_path):
    ledger = _ledger(tmp_path / "portfolio.sqlite")
    future = list(_risks())
    future[0] = DeskRiskEstimate("swing", AS_OF + timedelta(seconds=1),
                                 Decimal("1"), Decimal("1"), _hash("future"))
    result = _evaluate(ledger=ledger, policy=_policy(), desk_risks=tuple(future))
    assert result.disposition is AggregateRiskDisposition.BLOCKED_RISK_EVIDENCE


def test_one_desk_over_account_cap_is_blocked_even_if_sum_would_fit(tmp_path):
    ledger = _ledger(tmp_path / "portfolio.sqlite")
    risks = _risks(current_swing="4000", add_swing="2000",
                   current_options="0", add_options="0")
    result = _evaluate(ledger=ledger, policy=_policy(), desk_risks=risks)
    assert result.disposition is AggregateRiskDisposition.BLOCKED_RISK_EVIDENCE
    assert not result.order_permitted


def test_ambiguous_order_in_durable_journal_blocks_account_risk(tmp_path):
    ledger = _ledger(tmp_path / "portfolio.sqlite")
    journal = OrderJournal(tmp_path / "orders.sqlite")
    created = OrderEvent("ambiguous-1", "event-0", 0, "CREATED", AS_OF - timedelta(minutes=2),
                         10, 0, None, {})
    submitted = OrderEvent("ambiguous-1", "event-1", 1, "SUBMITTED",
                           AS_OF - timedelta(minutes=1, seconds=30),
                           10, 0, None, {})
    unknown = OrderEvent("ambiguous-1", "event-2", 2, "UNKNOWN", AS_OF - timedelta(minutes=1),
                         10, 0, None, {})
    journal.append(created)
    journal.append(submitted)
    journal.append(unknown)

    result = _evaluate(ledger=ledger, policy=_policy(), order_journal=journal)

    assert result.disposition is AggregateRiskDisposition.BLOCKED_ORDER_STATE_UNCERTAIN
    assert result.unresolved_order_ids == ("ambiguous-1",)
    assert result.order_audit_sha256
    assert not result.order_permitted


def test_corrupt_order_event_hash_fails_closed(tmp_path):
    ledger = _ledger(tmp_path / "portfolio.sqlite")
    journal_path = tmp_path / "orders.sqlite"
    journal = OrderJournal(journal_path)
    journal.append(OrderEvent("working-1", "event-0", 0, "CREATED", AS_OF - timedelta(minutes=2),
                              10, 0, None, {}))
    with sqlite3.connect(journal_path) as connection:
        connection.execute("UPDATE order_events SET event_json=? WHERE event_id='event-0'",
                           ('{"order_id":"working-1","event_id":"event-0","sequence":0,'
                            '"state":"CREATED","occurred_at":"2026-11-26T09:28:00+00:00",'
                            '"quantity":10,"filled_quantity":0,"price":null,"accounting":{},'
                            '"fill_evidence_id":null,"reconciliation_evidence_id":null}',))

    result = _evaluate(ledger=ledger, policy=_policy(), order_journal=journal)

    assert result.disposition is AggregateRiskDisposition.BLOCKED_ORDER_STATE_UNCERTAIN
    assert result.reason == "ORDER_AUDIT_FAILED:OrderJournalError"
    assert not result.order_permitted
