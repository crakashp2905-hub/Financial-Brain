from datetime import date, timedelta

import duckdb
import numpy as np
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


def research_with_universe(days=25, names=120):
    con = duckdb.connect()
    calendar = pd.bdate_range("2026-10-05", periods=days).date
    con.execute("""CREATE TABLE adjusted_prices
        (lineage VARCHAR, ticker VARCHAR, business_date DATE, close_adj DOUBLE, turnover DOUBLE, isin VARCHAR)""")
    con.execute("""CREATE TABLE features
        (lineage VARCHAR, business_date DATE, adv20 DOUBLE, vol_60 DOUBLE, mom_12_1 DOUBLE,
         dist_52w_high DOUBLE, ret_20d DOUBLE, ret_1d DOUBLE, ret_5d DOUBLE, rsi_14 DOUBLE,
         adx_14 DOUBLE, atr_pctile_250 DOUBLE, atr_14_pct DOUBLE)""")
    con.execute("CREATE TABLE security_lineage (isin VARCHAR, lineage VARCHAR)")
    con.execute("""CREATE TABLE universe_snapshots
        (business_date DATE, isin VARCHAR, exchange VARCHAR, ticker VARCHAR, series VARCHAR,
         instrument_type VARCHAR, traded_volume BIGINT, turnover DOUBLE, trades BIGINT,
         close_price DOUBLE, tradable BOOLEAN)""")
    prices, features, lineages, snapshots = [], [], [], []
    for i in range(names):
        isin, lineage, ticker = f"ISIN{i}", f"L{i}", f"T{i}"
        lineages.append((isin, lineage))
        for k, day in enumerate(calendar):
            price = 100 * np.exp(0.0005 * k * (i / names))
            prices.append((lineage, ticker, day, price, 2e8, isin))
            features.append((lineage, day, 2e8, 0.25, 0.25 + 0.001 * i, -0.1, 0.0, 0.0,
                             0.0, 50.0, 20.0, 0.5, 0.02))
        snapshots.append((calendar[0], isin, "NSE", ticker, "EQ", "STK", 1000, 2e8, 100,
                          100.0, i != 0))
    lineages.extend([("MISSINGPX", "LMISSING"), ("ORPHAN", None)])
    snapshots.extend([
        (calendar[0], "MISSINGPX", "NSE", "MISSINGPX", "EQ", "STK", 0, 0.0, 0, None, True),
        (calendar[0], "ORPHAN", "NSE", "ORPHAN", "EQ", "STK", 0, 0.0, 0, None, True),
    ])
    con.executemany("INSERT INTO adjusted_prices VALUES (?,?,?,?,?,?)", prices)
    con.executemany("INSERT INTO features VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", features)
    con.executemany("INSERT INTO security_lineage VALUES (?,?)", lineages)
    con.executemany("INSERT INTO universe_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", snapshots)
    return con, calendar


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


def test_full_universe_rejects_and_missing_paths_are_reported_with_action_sensitivity():
    research_con, calendar = research_with_universe()
    ledger = duckdb.connect()
    count = C.record(research_con, ledger, calendar[0])
    assert count["universe_attempts"] == 122
    assert ledger.execute("SELECT capture_status, attempted_n FROM champion_universe_capture").fetchone() == (
        "CAPTURED", 122)
    sample = ledger.execute("""SELECT reason, selected FROM champion_universe_attempts
        WHERE strategy=? AND isin='ISIN0'""", [C.CHAMPION]).fetchone()
    assert sample == ("NOT_TRADABLE", False)
    assert ledger.execute("""SELECT reason FROM champion_universe_attempts
        WHERE strategy=? AND isin='MISSINGPX'""", [C.CHAMPION]).fetchone()[0] == "MISSING_ADJUSTED_PRICE"
    assert ledger.execute("""SELECT reason FROM champion_universe_attempts
        WHERE strategy=? AND isin='ORPHAN'""", [C.CHAMPION]).fetchone()[0] == "MISSING_LINEAGE"

    research_con.execute("CREATE TABLE corporate_actions (action_id VARCHAR, isin VARCHAR, ex_date DATE)")
    research_con.execute("INSERT INTO corporate_actions VALUES ('ACT-1','ISIN1',?)", [calendar[10]])
    assert C.resolve(ledger, research_con, horizon=20) == 1
    row = ledger.execute("""SELECT attempted_n,rejected_n,missing_path_n,corporate_action_n,
        corporate_action_table_available,universe_coverage,universe_worst_case
        FROM champion_cohorts WHERE day=? AND strategy=?""", [calendar[0].isoformat(), C.CHAMPION]).fetchone()
    assert row[:5] == (122, row[1], 2, 1, True)
    assert row[1] >= 82 and row[5] < 1 and row[6] < 0
    ids = ledger.execute("""SELECT corporate_action_ids FROM champion_universe_attempts
        WHERE day=? AND strategy=? AND isin='ISIN1'""", [calendar[0].isoformat(), C.CHAMPION]).fetchone()[0]
    assert ids == '["ACT-1"]'

