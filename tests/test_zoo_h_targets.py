from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import edges as E
from financial_brain.paper import zoo_edges as ZE
from financial_brain.evaluation.edges import load as load_edge_panel


def _panel(n_names=40, n_days=300):
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2025-01-01", periods=n_days)
    cols = [f"L{i}" for i in range(n_names)]
    drift = np.r_[np.full(10, -0.004), np.linspace(0.001, 0.003, n_names - 10)]
    r = rng.normal(drift, 0.01, size=(n_days, n_names))
    C = pd.DataFrame(100 * np.exp(np.cumsum(r, axis=0)), index=idx, columns=cols)
    U = pd.DataFrame(True, index=idx, columns=cols)
    return {"C": C, "U": U}, cols


def _rcon(cols):
    con = duckdb.connect()
    ind = ["IND_A"] * 10 + ["IND_B"] * 10 + ["IND_C"] * 10 + ["IND_D"] * 10
    con.register("ap", pd.DataFrame({"lineage": cols, "isin": [f"IN{i}" for i in range(len(cols))],
                                      "business_date": pd.Timestamp("2025-01-01")}))
    con.execute("CREATE TABLE adjusted_prices AS SELECT * FROM ap")
    memberships = []
    for isin, industry in zip([f"IN{i}" for i in range(len(cols))], ind):
        memberships.extend([
            (isin, industry, "NIFTY 500", pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-02", tz="UTC")),
            (isin, "FUTURE_INDUSTRY", "NIFTY 500", pd.Timestamp("2026-03-01"), pd.Timestamp("2026-03-02", tz="UTC")),
        ])
    con.register("im", pd.DataFrame(memberships, columns=["isin", "industry", "index_name", "as_of", "observed_at"]))
    con.execute("CREATE TABLE index_membership AS SELECT * FROM im")
    return con


def test_h003_and_h005_pick_twenty_equal_weighted_names():
    P, cols = _panel()
    out = ZE.h_targets(_rcon(cols), P)
    h3, h5 = out["edge_h003_riskadj_mom"], out["edge_h005_industry_mom"]
    assert len(h3) == 20 and abs(sum(h3.values()) - 1) < 1e-9
    # H005 holds only names from the three best industries (the drift rises with the column index)
    assert h5 and set(h5) <= set(cols[10:])
    assert abs(sum(h5.values()) - 1) < 1e-9


@pytest.mark.parametrize("n_days", [180, 300])
def test_h003_abstains_on_short_history_or_zero_volatility(n_days):
    P, cols = _panel(n_days=n_days)
    if n_days == 300:
        P["C"] = pd.DataFrame(100.0, index=P["C"].index, columns=P["C"].columns)

    out = ZE.h_targets(_rcon(cols), P)

    assert "edge_h003_riskadj_mom" not in out
    assert "no eligible holdings" in out["_errors"]["edge_h003_riskadj_mom"]


def test_monthly_candidate_sizing_returns_whole_shares_cash_and_costs():
    result = ZE.size_monthly_candidate(
        strategy="edge_h003_riskadj_mom",
        targets={"L1": 0.5, "L2": 0.5},
        lineage_to_ticker={"L1": "AAA", "L2": "BBB"},
        prices={"AAA": 100.0, "BBB": 120.0},
        capital_inr=100_000.0,
        min_order_inr=1_000.0,
    )

    assert result["status"] == "SIZED"
    assert {row["lineage"] for row in result["positions"]} == {"L1", "L2"}
    assert all(isinstance(row["shares"], int) and row["shares"] > 0 for row in result["positions"])
    assert result["cash_committed_inr"] <= result["capital_inr"]
    assert result["cash_inr"] >= 0
    assert result["expected_round_trip_fees_inr"] > 0
    assert result["physical_orders_submitted"] is False


def test_monthly_candidate_sizing_preserves_missing_mapping_and_price_rejections():
    result = ZE.size_monthly_candidate(
        strategy="edge_h005_industry_mom",
        targets={"NO-MAP": 0.5, "NO-PRICE": 0.5},
        lineage_to_ticker={"NO-PRICE": "BBB"}, prices={}, capital_inr=100_000.0,
        min_order_inr=1_000.0,
    )

    assert result["status"] == "NO_EXECUTABLE_POSITIONS"
    assert {row["lineage"] for row in result["skipped"]} == {"NO-MAP", "NO-PRICE"}
    assert result["cash_inr"] == result["capital_inr"]


def test_monthly_candidate_sizing_rejects_duplicate_symbol_aliases_and_overspend_weights():
    with pytest.raises(ValueError, match="multiple target lineages"):
        ZE.size_monthly_candidate(
            strategy="edge_p3_frog_in_pan", targets={"L1": 0.5, "L2": 0.5},
            lineage_to_ticker={"L1": "AAA", "L2": "AAA"}, prices={"AAA": 100.0},
            capital_inr=100_000.0,
        )


def test_monthly_rebalance_plans_next_session_whole_share_intents():
    result = ZE.plan_monthly_rebalance(
        strategy="edge_h003_riskadj_mom", targets={"L1": 0.25, "L2": 0.25},
        current_shares={"L1": 100}, lineage_to_ticker={"L1": "AAA", "L2": "BBB"},
        prices={"AAA": 100.0, "BBB": 100.0},
        liquidity_bucket_by_ticker={"AAA": "large", "BBB": "large"},
        capital_inr=100_000.0, free_cash_inr=90_000.0,
        decision_date=pd.Timestamp("2026-11-27").date(),
        next_session_date=pd.Timestamp("2026-11-30").date(), turnover_budget_inr=100_000.0,
        cash_reserve_fraction=0.05, min_order_inr=1_000.0,
    )

    assert result["status"] == "READY_FOR_PAPER_REVIEW"
    assert result["next_session_date"] == "2026-11-30"
    assert {row["side"] for row in result["orders"]} == {"BUY"}
    assert all(row["status"] == "PAPER_REVIEW_REQUIRED" for row in result["orders"])
    assert result["cash_after_buy_intents_inr"] >= result["cash_reserve_inr"]
    assert result["planned_turnover_inr"] <= result["turnover_budget_inr"]
    assert result["physical_orders_submitted"] is False


def test_monthly_rebalance_rejects_over_budget_turnover_as_a_whole():
    result = ZE.plan_monthly_rebalance(
        strategy="edge_h003_riskadj_mom", targets={"L1": 0.25, "L2": 0.25},
        current_shares={"L1": 100}, lineage_to_ticker={"L1": "AAA", "L2": "BBB"},
        prices={"AAA": 100.0, "BBB": 100.0},
        liquidity_bucket_by_ticker={"AAA": "large", "BBB": "large"},
        capital_inr=100_000.0, free_cash_inr=90_000.0,
        decision_date=pd.Timestamp("2026-11-27").date(),
        next_session_date=pd.Timestamp("2026-11-30").date(), turnover_budget_inr=1_000.0,
        min_order_inr=1_000.0,
    )

    assert result["status"] == "REJECTED_TURNOVER_BUDGET"
    assert result["orders"] == []
    assert len(result["rejected"]) == 2


def test_monthly_rebalance_does_not_use_unsettled_sale_proceeds_for_buys():
    result = ZE.plan_monthly_rebalance(
        strategy="edge_h005_industry_mom", targets={"L1": 0.1, "L2": 0.4},
        current_shares={"L1": 200}, lineage_to_ticker={"L1": "AAA", "L2": "BBB"},
        prices={"AAA": 100.0, "BBB": 100.0},
        liquidity_bucket_by_ticker={"AAA": "large", "BBB": "large"},
        capital_inr=25_000.0, free_cash_inr=5_000.0,
        decision_date=pd.Timestamp("2026-11-27").date(),
        next_session_date=pd.Timestamp("2026-11-30").date(), turnover_budget_inr=30_000.0,
        cash_reserve_fraction=0.05, min_order_inr=1_000.0,
    )

    assert result["status"] == "READY_FOR_PAPER_REVIEW"
    assert result["orders"][0]["side"] == "SELL"
    buy = next(row for row in result["orders"] if row["side"] == "BUY")
    assert buy["shares"] < buy["requested_shares"]
    assert result["unsettled_sell_proceeds_inr"] > 0
    assert result["cash_after_buy_intents_inr"] >= result["cash_reserve_inr"]
    with pytest.raises(ValueError, match="cannot exceed"):
        ZE.size_monthly_candidate(
            strategy="edge_p3_frog_in_pan", targets={"L1": 0.75, "L2": 0.75},
            lineage_to_ticker={"L1": "AAA", "L2": "BBB"}, prices={"AAA": 100.0, "BBB": 100.0},
            capital_inr=100_000.0,
        )


def test_h005_data_failure_does_not_suppress_h003_target():
    P, cols = _panel()
    con = _rcon(cols)
    original = ZE._industries

    def fail_industries(*args, **kwargs):
        raise ValueError("missing industry membership")

    ZE._industries = fail_industries
    try:
        out = ZE.h_targets(con, P)
    finally:
        ZE._industries = original

    assert len(out["edge_h003_riskadj_mom"]) == 20
    assert "edge_h005_industry_mom" not in out
    assert "missing industry membership" in out["_errors"]["edge_h005_industry_mom"]


def test_monthly_p2_history_failure_does_not_suppress_other_books(monkeypatch):
    P, cols = _panel()
    con = _rcon(cols)
    last = P["C"].index[-1]
    monkeypatch.setattr(ZE, "_panel", lambda *_args: P)
    monkeypatch.setattr(E, "residual_scores", lambda panel: panel["C"].rank(axis=1))
    monkeypatch.setattr(E, "momentum_scores", lambda panel: panel["C"].rank(axis=1))
    monkeypatch.setattr(E, "info_discreteness", lambda panel: panel["C"].rank(axis=1))
    monkeypatch.setattr(E, "top_picks", lambda *_args: (_ for _ in ()).throw(ValueError("missing P2 history")))

    out = ZE.monthly_targets(con, last.date())

    assert "edge_p2_volmanaged_mom" not in out
    assert "missing P2 history" in out["_errors"]["edge_p2_volmanaged_mom"]
    assert {"edge_p1_residual_mom", "edge_p3_frog_in_pan", "edge_h003_riskadj_mom",
            "edge_h005_industry_mom"} <= out.keys()


def test_h005_skips_industries_with_fewer_than_eight_names():
    P, cols = _panel()
    con = _rcon(cols)
    con.execute("UPDATE index_membership SET industry = 'TINY' WHERE isin IN ('IN39', 'IN38')")
    out = ZE.h_targets(con, P)
    assert not {"L38", "L39"} & set(out["edge_h005_industry_mom"])


def test_h005_uses_only_industry_membership_known_at_rebalance():
    P, cols = _panel()
    con = _rcon(cols)
    as_of = P["C"].index[-1].date()
    industries = ZE._industries(con, cols, as_of)
    assert industries["L0"] == "IND_A"
    assert "FUTURE_INDUSTRY" not in set(industries)


def test_h005_excludes_membership_first_observed_after_rebalance_close():
    P, cols = _panel()
    con = _rcon(cols)
    as_of = P["C"].index[-1].date()
    late_observation = pd.Timestamp(as_of, tz="Asia/Kolkata") + pd.Timedelta(days=1)
    con.execute(
        "INSERT INTO index_membership VALUES (?, ?, ?, ?, ?)",
        ["IN0", "LATE_INDUSTRY", "NIFTY 500", as_of, late_observation],
    )

    industries = ZE._industries(con, cols, as_of)

    assert industries["L0"] == "IND_A"
    assert "LATE_INDUSTRY" not in set(industries)


def test_momentum_equity_universe_excludes_etfs_and_mutual_fund_units():
    con = duckdb.connect()
    day = pd.Timestamp("2026-11-24")
    con.register("prices", pd.DataFrame({
        "business_date": [day] * 3,
        "lineage": ["STOCK", "ETF", "MF"],
        "isin": ["INE000A01001", "INF000A01001", "INF000B01001"],
        "close_adj": [100.0, 100.0, 100.0],
    }))
    con.execute("CREATE TABLE adjusted_prices AS SELECT * FROM prices")
    con.register("features_input", pd.DataFrame({
        "business_date": [day] * 3,
        "lineage": ["STOCK", "ETF", "MF"],
        "adv20": [100.0, 1_000_000.0, 500_000.0],
    }))
    con.execute("CREATE TABLE features AS SELECT * FROM features_input")
    con.register("universe_input", pd.DataFrame({
        "business_date": [day] * 3,
        "isin": ["INE000A01001", "INF000A01001", "INF000B01001"],
        "exchange": ["NSE"] * 3,
        "series": ["EQ"] * 3,
        "instrument_type": ["STK", "ETF", "MF"],
        "tradable": [True] * 3,
    }))
    con.execute("CREATE TABLE universe_snapshots AS SELECT * FROM universe_input")

    panel = load_edge_panel(con, start="2026-11-24", n=1)

    assert list(panel["C"].columns) == ["STOCK"]
    assert panel["U"].loc[day, "STOCK"]


def test_monthly_paper_snapshot_persists_actual_fill_cash_and_weight_drift():
    z = duckdb.connect(":memory:")
    day = pd.Timestamp("2026-11-30").date()
    result = ZE.record_monthly_paper_snapshot(
        z, strategy="edge_h003_riskadj_mom", session=day,
        target_weights={"L1": 0.5, "L2": 0.5},
        fills=[{"order_id": "o1", "lineage": "L1", "side": "BUY",
                "requested_shares": 50, "filled_shares": 40,
                "fill_price": 100.0, "cost_inr": 10.0}],
        marks={"L1": 110.0}, opening_cash_inr=10_000.0,
    )
    assert result["closing_cash_inr"] == pytest.approx(5_990.0)
    assert result["holdings_value_inr"] == pytest.approx(4_400.0)
    assert result["equity_inr"] == pytest.approx(10_390.0)
    l1 = next(row for row in result["positions"] if row["lineage"] == "L1")
    l2 = next(row for row in result["positions"] if row["lineage"] == "L2")
    assert (l1["actual_shares"], l1["entry_shares"], l1["reason"]) == (40, 40, "ENTRY_FILLED")
    assert l1["drift"] == pytest.approx(4_400 / 10_390 - 0.5)
    assert (l2["actual_shares"], l2["reason"]) == (0, "NO_FILL_OR_REJECTED")
    assert z.execute("SELECT COUNT(*) FROM zoo_monthly_book_orders").fetchone()[0] == 1
    replay = ZE.record_monthly_paper_snapshot(
        z, strategy="edge_h003_riskadj_mom", session=day,
        target_weights={"L1": 0.5, "L2": 0.5},
        fills=[{"order_id": "o1", "lineage": "L1", "side": "BUY",
                "requested_shares": 50, "filled_shares": 40,
                "fill_price": 100.0, "cost_inr": 10.0}],
        marks={"L1": 110.0}, opening_cash_inr=10_000.0,
    )
    assert replay["idempotent_replay"] is True


def test_monthly_paper_snapshot_rolls_positions_forward_and_rejects_conflicts():
    z = duckdb.connect(":memory:")
    day1, day2 = pd.Timestamp("2026-11-30").date(), pd.Timestamp("2026-12-31").date()
    common = {"strategy": "edge_p1_residual_mom", "target_weights": {"L1": 0.5}}
    ZE.record_monthly_paper_snapshot(
        z, session=day1, **common,
        fills=[{"lineage": "L1", "side": "BUY", "requested_shares": 20,
                "filled_shares": 20, "fill_price": 100.0, "cost_inr": 5.0}],
        marks={"L1": 100.0}, opening_cash_inr=5_000.0,
    )
    result = ZE.record_monthly_paper_snapshot(
        z, session=day2, **common,
        fills=[{"lineage": "L1", "side": "SELL", "requested_shares": 5,
                "filled_shares": 5, "fill_price": 110.0, "cost_inr": 5.0}],
        marks={"L1": 110.0}, opening_cash_inr=2_995.0,
    )
    l1 = next(row for row in result["positions"] if row["lineage"] == "L1")
    assert (l1["actual_shares"], l1["exit_shares"], l1["reason"]) == (15, 5, "POSITION_ADJUSTED")
    assert result["closing_cash_inr"] == pytest.approx(3_540.0)
    with pytest.raises(ValueError, match="conflicting monthly paper snapshot"):
        ZE.record_monthly_paper_snapshot(
            z, session=day2, **common, fills=[], marks={"L1": 111.0}, opening_cash_inr=2_995.0)
    with pytest.raises(ValueError, match="sell fill exceeds paper holdings"):
        ZE.record_monthly_paper_snapshot(
            z, session=pd.Timestamp("2027-01-29").date(), **common,
            fills=[{"lineage": "L1", "side": "SELL", "requested_shares": 16,
                    "filled_shares": 16, "fill_price": 110.0, "cost_inr": 5.0}],
            marks={}, opening_cash_inr=result["closing_cash_inr"])


def test_monthly_paper_snapshot_rejects_oversell_and_unreasoned_no_fill():
    z = duckdb.connect(":memory:")
    with pytest.raises(ValueError, match="zero fills require"):
        ZE.record_monthly_paper_snapshot(
            z, strategy="edge_p2_volmanaged_mom", session=pd.Timestamp("2026-11-30").date(),
            target_weights={}, fills=[{"lineage": "L1", "side": "BUY", "requested_shares": 2,
                                       "filled_shares": 0, "cost_inr": 0}],
            marks={}, opening_cash_inr=1000.0)


def test_monthly_book_diversification_report_uses_aligned_net_returns_and_exposes_overlap():
    z = duckdb.connect(":memory:")
    from financial_brain.paper import zoo
    z.execute(zoo.SCHEMA)
    days = [pd.Timestamp(f"2026-11-{d:02d}").date() for d in (27, 28, 29)]
    left, right = "edge_p1_residual_mom", "edge_h003_riskadj_mom"
    for day in days:
        z.execute("INSERT INTO zoo_targets VALUES (?,?,?,?)", [left, day, "L1", 0.6])
        z.execute("INSERT INTO zoo_targets VALUES (?,?,?,?)", [left, day, "L2", 0.4])
        z.execute("INSERT INTO zoo_targets VALUES (?,?,?,?)", [right, day, "L1", 0.3])
        z.execute("INSERT INTO zoo_targets VALUES (?,?,?,?)", [right, day, "L3", 0.7])
    for strategy, values in ((left, [0.05, -0.1, 0.02]), (right, [0.02, None, 0.03])):
        for day, value in zip(days, values):
            z.execute("INSERT INTO zoo_pnl VALUES (?,?,?,?,?,?,?)",
                      [strategy, day, value, 0.0, value, 0.0, 2])
    report = ZE.monthly_book_diversification_report(
        z, strategies=(left, right), industry_by_lineage={"L1": "IND_A", "L2": "IND_A", "L3": "IND_B"},
        min_common_sessions=2)
    assert report["status"] == "SUFFICIENT_COMMON_HISTORY"
    assert report["common_net_return_sessions"] == 2
    assert report["return_coverage"][right]["missing_or_incomplete_sessions"] == 1
    pair = report["target_weighted_overlap"][0]
    assert pair["mean_weighted_jaccard"] == pytest.approx(0.3 / 1.7)
    assert report["net_return_correlation"][f"{left}|{right}"]["common_sessions"] == 2
    assert report["industry_concentration"][left]["mean_industry_hhi"] == pytest.approx(1.0)
    assert report["combination_diagnostic"]["not_a_promotion_or_allocation_decision"] is True


def test_monthly_book_diversification_report_marks_absent_industry_map_and_short_history():
    z = duckdb.connect(":memory:")
    from financial_brain.paper import zoo
    z.execute(zoo.SCHEMA)
    z.execute("INSERT INTO zoo_targets VALUES ('edge_p1_residual_mom', DATE '2026-11-27', 'L1', 1.0)")
    z.execute("INSERT INTO zoo_pnl VALUES ('edge_p1_residual_mom', DATE '2026-11-27', 0.01, 0, 0.01, 0, 1)")
    result = ZE.monthly_book_diversification_report(
        z, strategies=("edge_p1_residual_mom",), min_common_sessions=3)
    assert result["status"] == "INSUFFICIENT_COMMON_HISTORY"
    assert result["industry_concentration"]["edge_p1_residual_mom"]["status"] == \
        "UNAVAILABLE_NO_AS_OF_INDUSTRY_MAP"


def test_monthly_candidate_subperiod_report_keeps_fixed_params_and_descriptive_boundaries():
    from datetime import timedelta
    # Reuse the paper-engine market fixture with >3 years of sessions and deterministic signals.
    from tests.test_phase4_paper_engine import START, _db
    con = _db(names=4, sessions=1200, drift=0.001, minute_bars=False)
    report = ZE.monthly_candidate_subperiod_report(
        con, feature="dist_52w_high", start=START,
        end=START + timedelta(days=1199), rebalance=20, max_positions=3,
        min_adv=0.0, capital_inr=1_000_000.0)
    assert report["status"] == "DESCRIPTIVE_NOT_FOR_SELECTION"
    assert set(report["periods"]) == {"2024", "recent_half", "last_36_months"}
    assert report["periods"]["2024"]["status"] == "DESCRIPTIVE_ONLY"
    assert report["periods"]["last_36_months"]["sessions"] >= 40
    assert report["periods"]["2024"]["benchmark_variant"] == "PRICE"
    assert "Do not use them" in report["selection_warning"]
    assert "not prospective" in report["fills_note"]


def test_owner_sleeve_gate_requires_owner_approval_and_rejects_over_capital():
    plan = {"status": "READY_FOR_PAPER_REVIEW", "capital_inr": 10_000.0,
            "estimated_costs_inr": 50.0, "orders": [{"lineage": "L1", "side": "BUY"}]}
    kwargs = {"plan": plan, "owner_id": "owner-1", "approval_reference": "risk-review-v3",
              "approved_risk_sleeve_inr": 8_000.0, "available_risk_capital_inr": 8_000.0,
              "expected_gross_alpha_inr": 100.0, "alpha_evidence_sha256": "a" * 64}
    result = ZE.gate_monthly_plan_to_owner_sleeve(**kwargs)
    assert result["status"] == "REJECTED_CAPITAL_LIMIT"
    assert result["orders"] == []
    with pytest.raises(ValueError, match="approval reference"):
        ZE.gate_monthly_plan_to_owner_sleeve(**{**kwargs, "approval_reference": " "})


def test_owner_sleeve_gate_returns_cash_when_costs_exceed_or_alpha_is_unverified():
    plan = {"status": "READY_FOR_PAPER_REVIEW", "capital_inr": 5_000.0,
            "estimated_costs_inr": 75.0, "orders": [{"lineage": "L1", "side": "BUY"}]}
    base = {"plan": plan, "owner_id": "owner-1", "approval_reference": "approval-7",
            "approved_risk_sleeve_inr": 10_000.0, "available_risk_capital_inr": 8_000.0,
            "expected_gross_alpha_inr": 75.0, "alpha_evidence_sha256": "b" * 64}
    result = ZE.gate_monthly_plan_to_owner_sleeve(**base)
    assert result["status"] == "CASH_NO_TRADE"
    assert result["orders"] == []
    assert "does not exceed" in result["reason"]
    no_receipt = ZE.gate_monthly_plan_to_owner_sleeve(
        **{**base, "expected_gross_alpha_inr": None, "alpha_evidence_sha256": None})
    assert no_receipt["status"] == "CASH_NO_TRADE"
    assert "unavailable" in no_receipt["reason"]


def test_owner_sleeve_gate_passes_only_reviewable_paper_intents_with_edge_after_costs():
    plan = {"status": "READY_FOR_PAPER_REVIEW", "capital_inr": 5_000.0,
            "estimated_costs_inr": 25.0, "orders": [{"lineage": "L1", "side": "BUY"}]}
    result = ZE.gate_monthly_plan_to_owner_sleeve(
        plan=plan, owner_id="owner-1", approval_reference="approval-8",
        approved_risk_sleeve_inr=10_000.0, available_risk_capital_inr=8_000.0,
        expected_gross_alpha_inr=100.0, alpha_evidence_sha256="c" * 64)
    assert result["status"] == "READY_FOR_PAPER_REVIEW"
    assert result["orders"] == plan["orders"]
    assert result["physical_orders_submitted"] is False
