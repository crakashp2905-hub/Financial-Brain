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


def test_shared_account_loss_budget_sums_all_desks_and_binds_ledger_evidence(tmp_path):
    ledger = _ledger(tmp_path / "portfolio.sqlite")
    result = evaluate_aggregate_risk_budget(ledger=ledger, policy=_policy(), as_of=AS_OF,
                                            desk_risks=_risks())
    assert result.disposition is AggregateRiskDisposition.PERMITTED
    assert result.current_loss_risk_inr == Decimal("3000")
    assert result.projected_loss_risk_inr == Decimal("4500")
    assert result.remaining_loss_risk_inr == Decimal("500")
    assert result.ledger_events_sha256 and result.ledger_snapshot_sha256
    assert result.order_permitted
    assert len(result.decision_sha256) == 64


def test_cap_excess_denies_across_desks_and_risk_evidence_gaps_block(tmp_path):
    ledger = _ledger(tmp_path / "portfolio.sqlite")
    over = evaluate_aggregate_risk_budget(
        ledger=ledger, policy=_policy(maximum_loss_risk_inr=Decimal("4000")),
        as_of=AS_OF, desk_risks=_risks(),
    )
    assert over.disposition is AggregateRiskDisposition.DENIED_RISK_BUDGET
    assert not over.order_permitted

    missing_desk = evaluate_aggregate_risk_budget(
        ledger=ledger, policy=_policy(), as_of=AS_OF, desk_risks=_risks()[:1],
    )
    assert missing_desk.disposition is AggregateRiskDisposition.BLOCKED_RISK_EVIDENCE
    assert not missing_desk.order_permitted


def test_unresolved_missing_balance_stale_marks_and_payload_tamper_fail_closed(tmp_path):
    unresolved = evaluate_aggregate_risk_budget(
        ledger=_ledger(tmp_path / "unresolved.sqlite", unresolved=True),
        policy=_policy(), as_of=AS_OF, desk_risks=_risks(),
    )
    assert unresolved.disposition is AggregateRiskDisposition.BLOCKED_LEDGER_UNCERTAIN

    no_opening = evaluate_aggregate_risk_budget(
        ledger=_ledger(tmp_path / "no-opening.sqlite", opening=False),
        policy=_policy(), as_of=AS_OF, desk_risks=_risks(),
    )
    assert no_opening.disposition is AggregateRiskDisposition.BLOCKED_LEDGER_UNCERTAIN

    stale = evaluate_aggregate_risk_budget(
        ledger=_ledger(tmp_path / "stale.sqlite", mark_age_seconds=600),
        policy=_policy(), as_of=AS_OF, desk_risks=_risks(),
    )
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
    tampered = evaluate_aggregate_risk_budget(
        ledger=tampered_ledger, policy=_policy(), as_of=AS_OF, desk_risks=_risks(),
    )
    assert tampered.disposition is AggregateRiskDisposition.BLOCKED_LEDGER_UNCERTAIN
    assert "LEDGER_READ_FAILED" in tampered.reason


def test_future_or_mismatched_as_of_risk_estimate_blocks(tmp_path):
    ledger = _ledger(tmp_path / "portfolio.sqlite")
    future = list(_risks())
    future[0] = DeskRiskEstimate("swing", AS_OF + timedelta(seconds=1),
                                 Decimal("1"), Decimal("1"), _hash("future"))
    result = evaluate_aggregate_risk_budget(ledger=ledger, policy=_policy(), as_of=AS_OF,
                                            desk_risks=tuple(future))
    assert result.disposition is AggregateRiskDisposition.BLOCKED_RISK_EVIDENCE


def test_one_desk_over_account_cap_is_blocked_even_if_sum_would_fit(tmp_path):
    ledger = _ledger(tmp_path / "portfolio.sqlite")
    risks = _risks(current_swing="4000", add_swing="2000",
                   current_options="0", add_options="0")
    result = evaluate_aggregate_risk_budget(ledger=ledger, policy=_policy(), as_of=AS_OF,
                                            desk_risks=risks)
    assert result.disposition is AggregateRiskDisposition.BLOCKED_RISK_EVIDENCE
    assert not result.order_permitted
