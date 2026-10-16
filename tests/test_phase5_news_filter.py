"""The rejected-results filter: reaction window, market adjustment, bottom-fifth flag, 20-session expiry."""
from datetime import date

import duckdb
import pandas as pd

from financial_brain.decisions import news_filter as NF


def db(n_names=10, crash="L0", ann=date(2024, 3, 5)):
    con = duckdb.connect()
    days = pd.bdate_range("2024-01-01", "2024-04-30")
    rows, px = [], []
    for i in range(n_names):
        lin = f"L{i}"
        c = 100.0
        for d in days:
            if lin == crash and d == pd.Timestamp(ann) + pd.offsets.BDay(1):
                c *= 0.85                                            # the market rejects L0's results
            px.append((d.date(), lin, f"IN{i}", f"T{i}", c))
            rows.append((lin, d.date(), 1e9 - i, 0.0))
    con.execute("CREATE TABLE features (lineage VARCHAR, business_date DATE, adv20 DOUBLE, ret_1d DOUBLE)")
    con.executemany("INSERT INTO features VALUES (?,?,?,?)", rows)
    con.execute("CREATE TABLE adjusted_prices (business_date DATE, lineage VARCHAR, isin VARCHAR, ticker VARCHAR, close_adj DOUBLE)")
    con.executemany("INSERT INTO adjusted_prices VALUES (?,?,?,?,?)", px)
    con.execute("CREATE TABLE announcements (business_date DATE, isin VARCHAR, event_type VARCHAR)")
    con.executemany("INSERT INTO announcements VALUES (?,?,?)", [(ann, f"IN{i}", "RESULTS") for i in range(n_names)])
    return con


def test_a_rejected_result_is_flagged_and_expires_after_twenty_sessions():
    con = db()
    re = NF.reactions(con, date(2024, 3, 20))
    assert len(re) == 10 and re.set_index("lineage").at["L0", "react"] < -0.1
    assert set(NF.flagged(con, date(2024, 3, 20))) == {"L0"}
    assert NF.flagged(con, date(2024, 4, 30)) == {}                 # more than 20 sessions later


def test_nothing_is_flagged_before_the_reaction_window_closes():
    con = db()
    assert NF.flagged(con, date(2024, 3, 5)) == {}                  # announcement day: the session after has not closed
