from datetime import datetime, timezone

import duckdb
import pytest

from financial_brain.forward_edge.alerts import (
    AlertBook,
    AlertError,
    evaluate_desk_alerts,
    mutual_fund_pending_rows,
)


NOW = datetime(2026, 11, 22, 20, 0, tzinfo=timezone.utc)


def test_desk_alerts_cover_orders_stale_feeds_mf_etf_options_and_risk():
    alerts = evaluate_desk_alerts(
        session_id="paper-2026-11-22", occurred_at=NOW,
        order_states=({"order_id": "o1", "state": "PARTIAL"},
                      {"order_id": "o2", "state": "UNKNOWN"}),
        stale_feeds=({"feed_id": "kite:NSE:ABC", "age_seconds": 61, "entry_critical": True},),
        pending_mutual_funds=({"item_id": "allot-1", "stage": "UNITS", "age_days": 2},
                              {"item_id": "payout-1", "stage": "PAYOUT", "age_days": 2}),
        etf_premiums=({"instrument_id": "NSE:ETF1", "premium_discount_bps": -125.0},),
        option_expiries=({"contract_id": "NIFTY-2026-11-26-CE", "days_left": 2},),
        risk_budget_changes=({"budget_id": "portfolio", "old_value": 0.01, "new_value": 0.005},),
    )
    assert {alert.alert_type for alert in alerts} == {
        "ORDER_EXECUTION", "STALE_DATA", "MF_PENDING", "ETF_DISLOCATION",
        "OPTIONS_EXPIRY", "RISK_BUDGET_CHANGE",
    }
    assert len(alerts) == 8
    critical = next(alert for alert in alerts if alert.alert_type == "STALE_DATA")
    assert critical.severity == "HIGH"
    assert next(alert for alert in alerts if "o2" in alert.details).severity == "HIGH"


def test_alert_thresholds_suppress_ordinary_events_and_keep_exact_boundaries():
    alerts = evaluate_desk_alerts(
        session_id="s", occurred_at=NOW,
        stale_feeds=({"feed_id": "fresh", "age_seconds": 60},),
        pending_mutual_funds=({"item_id": "same-day", "stage": "PAYOUT", "age_days": 0},),
        etf_premiums=({"instrument_id": "NSE:ETF", "premium_discount_bps": 100},),
        option_expiries=({"contract_id": "C", "days_left": 4},),
        risk_budget_changes=({"budget_id": "risk", "old_value": 0.1, "new_value": 0.1},),
    )
    assert alerts == []


def test_desk_alerts_deduplicate_in_durable_alert_book(tmp_path):
    alerts = evaluate_desk_alerts(
        session_id="s", occurred_at=NOW,
        order_states=({"order_id": "o1", "state": "FILLED"},),
    )
    book = AlertBook(tmp_path / "alerts.sqlite3")
    try:
        assert book.raise_alert(alerts[0]) is True
        assert book.raise_alert(alerts[0]) is False
        assert book.active("s")[0]["alert_type"] == "ORDER_EXECUTION"
    finally:
        book.close()


@pytest.mark.parametrize("bad", [
    {"stale_after_seconds": float("nan")},
    {"mf_pending_after_days": -1},
    {"option_expiry_window_days": 1.5},
])
def test_desk_alert_thresholds_fail_closed(bad):
    with pytest.raises(AlertError):
        evaluate_desk_alerts(session_id="s", occurred_at=NOW, **bad)


def test_mf_pending_adapter_reads_only_point_in_time_unresolved_units_and_payouts():
    con = duckdb.connect(":memory:")
    try:
        con.execute("""CREATE TABLE mf_paper_orders (
            order_id VARCHAR, account_id VARCHAR, transaction_type VARCHAR, created_at TIMESTAMPTZ)""")
        con.execute("""CREATE TABLE mf_paper_purchase_eligibility (
            order_id VARCHAR, state VARCHAR, transaction_at TIMESTAMPTZ, updated_at TIMESTAMPTZ)""")
        con.execute("""CREATE TABLE mf_paper_redemption_settlements (
            account_id VARCHAR, settlement_id VARCHAR, state VARCHAR, requested_on DATE,
            created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ)""")
        con.execute("""INSERT INTO mf_paper_orders VALUES
            ('purchase-1','paper-1','PURCHASE',TIMESTAMPTZ '2026-11-22 10:00:00+00'),
            ('purchase-future','paper-1','PURCHASE',TIMESTAMPTZ '2026-11-23 10:00:00+00')""")
        con.execute("""INSERT INTO mf_paper_purchase_eligibility VALUES
            ('purchase-1','NAV_PENDING',TIMESTAMPTZ '2026-11-22 10:00:00+00',TIMESTAMPTZ '2026-11-22 10:00:00+00'),
            ('purchase-future','NAV_PENDING',TIMESTAMPTZ '2026-11-23 10:00:00+00',TIMESTAMPTZ '2026-11-23 10:00:00+00')""")
        con.execute("""INSERT INTO mf_paper_redemption_settlements VALUES
            ('paper-1','redeem-units','REDEMPTION_PENDING',DATE '2026-11-22',
                TIMESTAMPTZ '2026-11-22 10:00:00+00',TIMESTAMPTZ '2026-11-22 10:00:00+00'),
            ('paper-1','redeem-payout','PAYOUT_PENDING',DATE '2026-11-21',
                TIMESTAMPTZ '2026-11-21 10:00:00+00',TIMESTAMPTZ '2026-11-21 10:00:00+00'),
            ('paper-1','paid','CASH_AVAILABLE',DATE '2026-11-20',
                TIMESTAMPTZ '2026-11-20 10:00:00+00',TIMESTAMPTZ '2026-11-20 10:00:00+00')""")

        rows = mutual_fund_pending_rows(con, as_of=datetime(2026, 11, 22, 20, 0, tzinfo=timezone.utc))
        assert rows == (
            {"item_id": "paper-1:purchase-1", "stage": "UNITS", "age_days": 1},
            {"item_id": "paper-1:redeem-payout", "stage": "PAYOUT", "age_days": 2},
            {"item_id": "paper-1:redeem-units", "stage": "UNITS", "age_days": 1},
        )
    finally:
        con.close()
