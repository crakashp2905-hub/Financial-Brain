"""Point-in-time fundamental observations: revisions kept, nothing filed after the day, ratios from known values."""
from datetime import date, datetime

import duckdb
import pytest

from financial_brain.features import fundamentals_pit as FP


def db():
    con = duckdb.connect()
    con.execute("""CREATE TABLE xbrl_filings (seq_number VARCHAR, symbol VARCHAR, isin VARCHAR, period_end DATE,
                   filed_at TIMESTAMP, basis VARCHAR, status VARCHAR)""")
    con.execute("CREATE TABLE xbrl_facts (seq_number VARCHAR, concept VARCHAR, ctx VARCHAR, value DOUBLE)")
    q = [("2025-03-31", 1), ("2025-06-30", 2), ("2025-09-30", 3), ("2025-12-31", 4)]
    for pe, k in q:
        sid = f"s{k}"
        con.execute("INSERT INTO xbrl_filings VALUES (?,?,?,?,?,?,?)",
                    [sid, "ABC", "INE1", pe, datetime(2026, k, 10, 17, 0), "Consolidated", "stored"])
        for c, v in (("RevenueFromOperations", 1000), ("ProfitBeforeTax", 130), ("FinanceCosts", 20),
                     ("DepreciationDepletionAndAmortisationExpense", 30), ("OtherIncome", 10), ("ProfitLossForPeriod", 100),
                     ("BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations", 10.0),
                     ("PaidUpValueOfEquityShareCapital", 100), ("FaceValueOfEquityShareCapital", 10)):
            con.execute("INSERT INTO xbrl_facts VALUES (?,?,?,?)", [sid, c, "Q", v])
    for c, v in (("Equity", 2000), ("BorrowingsCurrent", 300), ("BorrowingsNoncurrent", 200), ("CashAndCashEquivalents", 100)):
        con.execute("INSERT INTO xbrl_facts VALUES (?,?,?,?)", ["s4", c, "I", v])
    # a restatement of the last quarter, filed later
    con.execute("INSERT INTO xbrl_filings VALUES (?,?,?,?,?,?,?)", ["s5", "ABC", "INE1", "2025-12-31", datetime(2026, 6, 1), "Consolidated", "stored"])
    con.execute("INSERT INTO xbrl_facts VALUES (?,?,?,?)", ["s5", "ProfitLossForPeriod", "Q", 60])
    return con


def test_revisions_are_kept_and_numbered():
    con = db()
    assert FP.build(con) > 0
    r = con.execute("SELECT revision FROM fundamental_observation WHERE seq_number = 's5'").fetchall()
    assert {x[0] for x in r} == {2}


def test_nothing_filed_after_the_day_is_known_and_the_restatement_appears_only_after_its_filing():
    con = db()
    FP.build(con)
    assert len(FP.as_known_on(con, "ABC", date(2026, 1, 9))) == 0                  # first filing is 10 Jan
    before = FP.snapshot(FP.as_known_on(con, "ABC", date(2026, 5, 1)))
    after = FP.snapshot(FP.as_known_on(con, "ABC", date(2026, 6, 2)))
    assert before["ttm_pat"] == 400 and after["ttm_pat"] == 360


def test_ratios_come_only_from_known_values():
    con = db()
    FP.build(con)
    s = FP.snapshot(FP.as_known_on(con, "ABC", date(2026, 5, 1)))
    assert s["ttm_ebitda"] == pytest.approx(4 * (130 + 20 + 30 - 10)) and s["debt"] == 500
    r = FP.ratios(s, price=200.0)
    assert r["market_cap"] == 2000 and r["pe"] == pytest.approx(5.0) and r["pb"] == pytest.approx(1.0)
    assert r["debt_equity"] == pytest.approx(0.25) and r["interest_cover"] == pytest.approx(600 / 80)
    assert r["ev_ebitda"] == pytest.approx((2000 + 400) / 680)
