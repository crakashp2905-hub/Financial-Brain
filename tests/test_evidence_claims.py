"""Every displayed opportunity makes exactly one of four claims, and a claim needs every tier below it."""
from financial_brain.evaluation import evidence_tiers as ET
from financial_brain.paper import terminal as T


def test_claims_are_monotone():
    assert ET.claim_tier(replicated=False, paper_forward=True, confirmed=True) == ET.OBSERVATION
    assert ET.claim_tier(replicated=True, paper_forward=False, confirmed=True) == ET.REPLICATED
    assert ET.claim_tier(replicated=True, paper_forward=True, confirmed=False) == ET.FORWARD_PAPER
    assert ET.claim_tier(replicated=True, paper_forward=True, confirmed=True) == ET.CONFIRMED


def test_the_terminal_list_is_an_observation_and_says_it_is_not_a_recommendation():
    claim = ET.list_claim(ET.LIST_STRATEGY["terminal list"])      # ranking v1 failed its walk-forward
    assert claim == ET.OBSERVATION
    note = T._claim_note(claim, "Every row below is a")
    assert "Not a trade recommendation" in note and "cash" in note
    assert "confirmed" not in T._tier(claim).split("'")[1]
    assert T._claim_note(ET.CONFIRMED, "x") == ""


def test_risk_filters_only_remove_and_cash_is_explicit():
    import pandas as pd
    from financial_brain.decisions import risk_filters as RF
    best = pd.DataFrame({"ticker": ["A", "B", "C", "D", "E"], "instrument": ["a", "b", "c", "d", "e"],
                         "flagged_by": ["momentum", "low_volatility", "low_volatility", "momentum", "low_volatility"],
                         "expected_net": [0.03, 0.002, -0.01, 0.04, 0.05], "cost": [0.004] * 5})
    liq = pd.Series({"e": 0.4, "a": 1.1, "d": 1.0})
    f = RF.apply(best, regime="CRISIS", liquidity=liq)
    assert list(f["filtered"]) == ["crisis_momentum", "edge_below_cost", "no_edge", "crisis_momentum", "falling_liquidity"]
    assert RF.cash_share(f, slots=5) == 1.0
    calm = RF.apply(best, regime="RISK_ON", liquidity=liq)
    assert list(calm["filtered"] == "") == [True, False, False, True, False]
    assert RF.cash_share(calm, slots=5) == 0.6 and len(calm) == len(best)


def test_a_lagging_input_makes_the_page_say_stale():
    from datetime import date, datetime
    import duckdb
    con = duckdb.connect()
    for t in ("eod_prices", "adjusted_prices", "features", "market_regime"):
        con.execute(f"CREATE TABLE {t} (business_date DATE)")
        con.execute(f"INSERT INTO {t} VALUES (?)", [date(2026, 10, 15)])
    con.execute("UPDATE features SET business_date = DATE '2026-10-14'")
    late = T.freshness(con, now=datetime(2026, 10, 15, 21, 0))          # Thursday evening, after the refresh
    assert len(late) == 1 and late[0].startswith("features ends 2026-10-14")
    assert "STALE INPUTS" in T._freshness_banner(late)
    assert T.freshness(con, now=datetime(2026, 10, 15, 10, 0)) == []       # morning: yesterday's data is current
