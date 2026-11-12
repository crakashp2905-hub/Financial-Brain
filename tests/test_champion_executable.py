from datetime import date, timedelta

import duckdb
import pandas as pd
import pytest

from financial_brain.evaluation import champion as C


def inputs(*, horizon=2, order_status="CANCELED", include_exit_mark=True):
    start = date(2026, 10, 5)
    days = [(start + timedelta(days=i)).isoformat() for i in range(horizon + 1)]
    calendar = days + [(start + timedelta(days=horizon + 1)).isoformat()]
    scheduled = pd.DataFrame([{"cohort_id": "C-1", "day": days[0], "strategy": C.CHAMPION, "capital": 1000.0}])
    evidence = pd.DataFrame([
        {"cohort_id": "C-1", "day": day, "complete": True, "corporate_actions_checked": True}
        for day in days
    ])
    orders = pd.DataFrame([
        {"cohort_id": "C-1", "order_id": "O-1", "strategy": C.CHAMPION, "day": days[0],
         "submitted_at_utc": f"{days[0]}T04:00:00Z", "symbol": "ABC", "side": "BUY",
         "requested_quantity": 10, "status": order_status},
        {"cohort_id": "C-1", "order_id": "O-2", "strategy": C.CHAMPION, "day": days[1],
         "submitted_at_utc": f"{days[1]}T04:00:00Z", "symbol": "XYZ", "side": "BUY",
         "requested_quantity": 2, "status": "REJECTED"},
    ])
    fills = pd.DataFrame([{
        "order_id": "O-1", "fill_id": "F-1", "day": days[0], "filled_at_utc": f"{days[0]}T04:01:00Z",
        "quantity": 5, "price": 100.0, "fees": 1.0,
    }])
    marks = pd.DataFrame([
        {"day": day, "symbol": "ABC", "price": price, "source": "test-fixture",
         "observed_at_utc": f"{day}T10:00:00Z"}
        for day, price in zip(days, [100.0, 105.0, 120.0][:horizon + 1])
        if include_exit_mark or day != days[-1]
    ])
    return scheduled, calendar, evidence, orders, fills, marks


def resolve(args, *, horizon=2):
    scheduled, calendar, evidence, orders, fills, marks = args
    ledger = duckdb.connect()
    count = C.resolve_executable(ledger, scheduled=scheduled, exchange_sessions=calendar,
                                 session_evidence=evidence, orders=orders, fills=fills, marks=marks,
                                 horizon=horizon)
    return ledger, count


def test_executable_cohort_uses_actual_partial_fill_fees_marks_and_cash_days():
    ledger, count = resolve(inputs())
    assert count == 1
    row = ledger.execute("""SELECT gross, cost, net, complete, cash_days, selected_n, n, no_fill_n,
        rejected_n, partial_n, metric_version
        FROM champion_executable_cohorts""").fetchone()
    assert row[:3] == pytest.approx((0.10, 0.001, 0.099))
    assert row[3:] == (True, 2, 2, 1, 0, 1, 1, C.EXECUTABLE_METRIC)
    # Executable rows take precedence; close-to-close diagnostics remain intact.
    ledger.execute("""INSERT INTO champion_cohorts
        (day,strategy,n,gross,cost,net,universe,complete,metric_version)
        VALUES ('2026-10-05', ?, 1, 50, 0, 50, 0, TRUE, ?)""",
                   [C.CHAMPION, C.RANKING_METRIC])
    ledger.execute("""INSERT INTO champion_cohorts
        (day,strategy,n,gross,cost,net,universe,complete,metric_version)
        VALUES ('2026-10-05', 'challenger', 1, 80, 0, 80, 0, TRUE, ?)""", [C.RANKING_METRIC])
    ledger.execute("""INSERT INTO champion_executable_cohorts
        (day,strategy,cohort_id,n,gross,cost,net,capital,selected_n,missing_n,complete,metric_version,
         cash_days,no_fill_n,rejected_n,partial_n,start_day,end_day)
        VALUES ('2026-10-05','challenger','C-CH',1,0,0,-0.02,1000,1,0,TRUE,?,0,0,0,0,
                '2026-10-05','2026-10-07')""", [C.EXECUTABLE_METRIC])
    assert ledger.execute("SELECT net FROM champion_cohorts").fetchone()[0] == 50
    report = C.standings(ledger, scheduled_days=["2026-10-05"], eligible_days={"2026-10-05"})[0]
    assert report["non_executable_cohorts"] == 0
    assert report["mean_net_vs_champion"] == pytest.approx(-0.119)


def test_explicit_no_order_cash_cohort_resolves_to_zero_instead_of_disappearing():
    args = list(inputs())
    args[3] = args[3].iloc[0:0]
    args[4] = args[4].iloc[0:0]
    ledger, _ = resolve(tuple(args))
    row = ledger.execute("SELECT net, complete, cash_days, selected_n, no_fill_n FROM champion_executable_cohorts").fetchone()
    assert row == (0.0, True, 3, 0, 0)


def test_sell_fills_release_inventory_and_include_sell_fee_in_net_nav():
    args = list(inputs())
    orders = args[3].copy()
    orders.loc[orders["order_id"] == "O-2", ["symbol", "side", "requested_quantity", "status"]] = [
        "ABC", "SELL", 2, "FILLED"]
    args[3] = orders
    fills = args[4].copy()
    fills.loc[len(fills)] = {
        "order_id": "O-2", "fill_id": "F-2", "day": "2026-10-06",
        "filled_at_utc": "2026-10-06T04:01:00Z", "quantity": 2, "price": 110.0, "fees": 0.5,
    }
    args[4] = fills
    ledger, _ = resolve(tuple(args))
    gross, cost, net, complete = ledger.execute(
        "SELECT gross,cost,net,complete FROM champion_executable_cohorts").fetchone()
    assert (gross, cost, net, complete) == pytest.approx((0.08, 0.0015, 0.0785, True))


def test_missing_mark_or_unresolved_order_withholds_cohort_return():
    args = list(inputs(include_exit_mark=False))
    ledger, _ = resolve(tuple(args))
    assert ledger.execute("SELECT complete, net FROM champion_executable_cohorts").fetchone() == (False, None)

    args = list(inputs(order_status="UNKNOWN"))
    ledger, _ = resolve(tuple(args))
    assert ledger.execute("SELECT complete, net FROM champion_executable_cohorts").fetchone() == (False, None)


def test_overlapping_frozen_cohorts_are_rejected():
    args = list(inputs())
    second = args[0].iloc[0].copy()
    second["cohort_id"] = "C-2"
    second["day"] = "2026-10-06"
    args[0] = pd.concat([args[0], pd.DataFrame([second])], ignore_index=True)
    with pytest.raises(C.ChampionError, match="overlapping"):
        resolve(tuple(args))

