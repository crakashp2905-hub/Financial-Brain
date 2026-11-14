from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.shadow_counterfactual import (
    ShadowBookJournal,
    ShadowCounterfactualError,
    ShadowFillPolicy,
    simulate_rejected_shadow_book,
)


UTC = timezone.utc


def _stamp(hour, minute, second=0):
    return datetime(2026, 1, 2, hour, minute, second, tzinfo=UTC).isoformat()


def _decision(decision_id="d1", **changes):
    row = {"decision_id": decision_id, "disposition": "REJECTED_FILTER", "reason": "spread_gate",
           "manifest_sha256": "a" * 64, "symbol": "ABC", "side": "BUY", "quantity": 10,
           "limit_price": 100.5, "cash_budget_inr": 2000.0,
           "decision_at_utc": _stamp(3, 0), "entry_deadline_utc": _stamp(3, 2),
           "exit_at_utc": _stamp(4, 0), "close_deadline_utc": _stamp(4, 5)}
    row.update(changes)
    return row


def _quote(event_id, minute, second=0, *, bid=99.9, ask=100.0, bid_size=10, ask_size=10, age=1):
    observed = datetime(2026, 1, 2, 3, minute, second, tzinfo=UTC)
    return {"event_id": event_id, "symbol": "ABC",
            "exchange_at_utc": (observed - timedelta(seconds=age)).isoformat(),
            "observed_at_utc": observed.isoformat(), "bid": bid, "ask": ask,
            "bid_size": bid_size, "ask_size": ask_size}


def _close(event_id="close", *, bid=102.0, bid_size=10):
    return {"event_id": event_id, "symbol": "ABC", "exchange_at_utc": _stamp(4, 0),
            "observed_at_utc": _stamp(4, 0, 1), "bid": bid, "ask": bid + 0.1,
            "bid_size": bid_size, "ask_size": 10}


def _policy():
    return ShadowFillPolicy("quote-depth-v1", max_quote_age_seconds=5,
                            modeled_round_trip_cost_bp=10)


def test_quote_depth_fills_are_costed_fifo_and_never_labeled_cash_pnl():
    report = simulate_rejected_shadow_book(
        decisions=[_decision()],
        quotes=[_quote("entry-a", 1, ask=100.0, ask_size=4),
                _quote("entry-b", 1, 10, ask=100.2, ask_size=6), _close()],
        policy=_policy(),
    )
    order = report["orders"][0]
    assert order["status"] == "CLOSED"
    assert order["entered_quantity"] == order["closed_quantity"] == 10
    assert order["entry_vwap"] == pytest.approx(100.12)
    assert order["close_vwap"] == 102.0
    assert order["gross_hypothetical_pnl_inr"] == pytest.approx(18.8)
    assert order["net_hypothetical_pnl_inr"] == pytest.approx(17.7988)
    assert report["evidence_class"] == "EXPLORATORY_SHADOW"
    assert report["observed_cash_pnl"] is False and report["promotion_eligible"] is False


def test_nonmarketable_order_is_no_fill_not_midpoint_or_ltp_interpolated():
    report = simulate_rejected_shadow_book(
        decisions=[_decision(limit_price=99.0)], quotes=[_quote("entry", 1, ask=100.0), _close()],
        policy=_policy(),
    )
    assert report["orders"][0]["status"] == "NO_ENTRY_FILL"
    assert report["orders"][0]["net_hypothetical_pnl_inr"] is None
    assert report["net_hypothetical_pnl_inr"] == 0


def test_partial_entry_and_unclosed_quantity_remain_incomplete_not_full_pnl():
    report = simulate_rejected_shadow_book(
        decisions=[_decision()],
        quotes=[_quote("entry", 1, ask_size=4), _close(bid_size=2)], policy=_policy(),
    )
    order = report["orders"][0]
    assert order["status"] == "UNRESOLVED_CLOSE"
    assert order["entered_quantity"] == 4 and order["closed_quantity"] == 2
    assert order["unclosed_quantity"] == 2
    assert report["complete_closed_order_count"] == 0
    assert report["net_hypothetical_pnl_inr"] == 0
    assert report["incomplete_close_hypothetical_pnl_inr"] is not None


def test_buy_budget_is_shared_across_partial_entry_quotes():
    report = simulate_rejected_shadow_book(
        decisions=[_decision(cash_budget_inr=400.0)],
        quotes=[_quote("entry-a", 1, ask=100.0, ask_size=4),
                _quote("entry-b", 1, 10, bid=89.9, ask=90.0, ask_size=10), _close()],
        policy=_policy(),
    )
    order = report["orders"][0]
    assert order["entered_quantity"] == 4
    assert order["status"] == "CLOSED_PARTIAL_ENTRY"


def test_quote_depth_is_shared_between_rejected_orders_and_not_reused():
    report = simulate_rejected_shadow_book(
        decisions=[_decision("d1", quantity=1), _decision("d2", quantity=1)],
        quotes=[_quote("only-entry", 1, ask_size=1), _close()], policy=_policy(),
    )
    assert report["quote_depth_consumption_is_shared"] is True
    assert sum(order["entered_quantity"] for order in report["orders"]) == 1
    assert {order["status"] for order in report["orders"]} == {"CLOSED", "NO_ENTRY_FILL"}


def test_registered_controls_do_not_upgrade_shadow_evidence_or_allow_promotion():
    report = simulate_rejected_shadow_book(decisions=[_decision()], quotes=[], policy=_policy(),
                                           registered_control_ids=["registered-control-7"])
    assert report["registered_control_ids"] == ["registered-control-7"]
    assert report["promotion_eligible"] is False
    assert "SHADOW_RESULTS_REQUIRE_SEPARATE_REGISTERED_TRIAL_AND_REVIEW" in report["promotion_blockers"]


def test_shadow_journal_is_idempotent_and_cannot_replace_a_book(tmp_path):
    report = simulate_rejected_shadow_book(decisions=[_decision()], quotes=[], policy=_policy())
    journal = ShadowBookJournal(tmp_path / "shadow.jsonl")
    digest = journal.record("rejected-opportunities-v1", report)
    assert journal.record("rejected-opportunities-v1", report) == digest
    changed = {**report, "net_hypothetical_pnl_inr": 100000.0}
    with pytest.raises(ShadowCounterfactualError, match="immutable"):
        journal.record("rejected-opportunities-v1", changed)


def test_counterfactual_memory_retains_rejection_without_claiming_actual_fills(tmp_path):
    report = simulate_rejected_shadow_book(
        decisions=[_decision("rejected-42")], quotes=[_quote("entry", 1), _close()], policy=_policy())
    journal = ShadowBookJournal(tmp_path / "counterfactual.jsonl")
    digest = journal.record("rejected-memory-v1", report)
    stored = journal.journal.records()[0]
    decision = stored["report"]["orders"][0]
    assert stored["sha256"] == digest
    assert decision["decision_nature"] == "HYPOTHETICAL_REJECTED_OPPORTUNITY"
    assert decision["decision_id"] == "rejected-42"
    assert decision["execution_observed"] is False and decision["counterfactual_only"] is True
    assert decision["status"] == "CLOSED"  # hypothetical quote simulation, never an actual fill


def test_invalid_quote_or_nonrejected_opportunity_fails_closed():
    with pytest.raises(ShadowCounterfactualError, match="only explicitly rejected"):
        simulate_rejected_shadow_book(decisions=[_decision(disposition="TAKEN")], quotes=[], policy=_policy())
    with pytest.raises(ShadowCounterfactualError, match="non-crossed"):
        simulate_rejected_shadow_book(decisions=[_decision()], quotes=[_quote("crossed", 1, bid=101, ask=100)],
                                      policy=_policy())
