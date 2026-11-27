from financial_brain.portfolio.legacy_exit_adjudication import (
    LegacyExitStatus,
    classify_legacy_exits,
    report_body,
)


def _unresolved(event_id="old-trade:unresolved", quantity="2"):
    return {
        "account_id": "intraday:live-v1:live", "sleeve_id": "ORB",
        "source_system": "paper_live.duckdb:trades", "source_event_id": event_id,
        "source_sha256": "a" * 64, "event_kind": "UNRESOLVED_POSITION",
        "occurred_at": "2026-11-03T10:00:00+05:30", "currency": "INR",
        "asset_class": "EQUITY", "instrument_id": "NSE:EXAMPLE", "quantity": quantity,
        "evidence_class": "LOCAL_PAPER", "metadata": {"legacy_trade_id": "old-trade", "trade_side": "long"},
    }


def _receipt(*, event_id="broker-trade-1", quantity="2"):
    return {
        "account_id": "intraday:live-v1:live", "sleeve_id": "ORB",
        "source_system": "BROKER_EXECUTION_RECEIPT", "source_event_id": event_id,
        "source_sha256": "b" * 64, "event_kind": "FILL", "occurred_at": "2026-11-03T11:00:00+05:30",
        "currency": "INR", "asset_class": "EQUITY", "instrument_id": "NSE:EXAMPLE", "side": "SELL",
        "quantity": quantity, "price": "101", "multiplier": "1", "evidence_class": "BROKER_EXECUTION_RECEIPT",
        "metadata": {"resolves_unresolved_event_id": "old-trade:unresolved", "legacy_trade_id": "old-trade",
                     "broker_order_id": "order-1", "broker_trade_id": event_id},
    }


def test_absent_receipt_keeps_each_position_open_unknown():
    rows = classify_legacy_exits([_unresolved()])
    assert len(rows) == 1
    assert rows[0].classification is LegacyExitStatus.OPEN_UNKNOWN
    assert rows[0].exit_fill_inferred is False


def test_partial_or_conflicting_receipt_does_not_confirm_close():
    rows = classify_legacy_exits([_unresolved(), _receipt(quantity="1")])
    assert rows[0].classification is LegacyExitStatus.LEDGER_INCOMPLETE


def test_exact_source_bound_receipt_is_the_only_confirmed_close_path():
    rows = classify_legacy_exits([_unresolved(), _receipt()])
    assert rows[0].classification is LegacyExitStatus.CONFIRMED_CLOSED


def test_report_uses_hashed_case_references_and_blocks_pnl_for_unknowns():
    report = report_body(events=[_unresolved()], source_snapshot_sha256="c" * 64,
                         snapshot_as_of_utc="2026-11-21T09:10:40+00:00")
    assert report["classification_counts"]["OPEN_UNKNOWN"] == 1
    assert report["pnl_release_authorized"] is False
    assert "old-trade" not in str(report)


def test_all_51_legacy_missing_exits_remain_open_unknown_without_inferred_fills():
    events = [_unresolved(event_id=f"legacy-trade-{index:02d}:unresolved") for index in range(51)]
    rows = classify_legacy_exits(events)
    report = report_body(events=events, source_snapshot_sha256="c" * 64,
                         snapshot_as_of_utc="2026-11-27T12:00:00+00:00")

    assert len(rows) == 51
    assert all(row.classification is LegacyExitStatus.OPEN_UNKNOWN for row in rows)
    assert all(row.exit_fill_inferred is False for row in rows)
    assert report["classification_counts"] == {
        "OPEN_UNKNOWN": 51, "LEDGER_INCOMPLETE": 0, "CONFIRMED_CLOSED": 0}
    assert report["pnl_release_authorized"] is False
