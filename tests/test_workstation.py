"""Calendar dates come from the filings' own text and the exchange's expiry rule; the ticket preview never sends."""
from datetime import date

import duckdb

from financial_brain.paper import workstation as W


def test_expiry_is_the_last_tuesday():
    assert W.expiries(date(2026, 10, 16)) == [date(2026, 10, 27), date(2026, 11, 24)]
    assert W.last_tuesday(2026, 9) == date(2026, 9, 29)          # matches the stored contracts


def test_board_meeting_date_is_parsed_from_the_intimation():
    con = duckdb.connect()
    con.execute("CREATE TABLE announcements (company VARCHAR, isin VARCHAR, headline VARCHAR, business_date DATE, event_type VARCHAR)")
    con.execute("INSERT INTO announcements VALUES ('X Ltd', 'INE1', 'X has informed BSE that the meeting of the Board is "
                "scheduled on 03/11/2026 ,inter alia, to consider the financial results', DATE '2026-10-14', 'BOARD_MEETING'), "
                "('Y Ltd', 'INE2', 'meeting scheduled on 31/12/2026', DATE '2026-10-14', 'BOARD_MEETING')")
    bm = W.board_meetings(con, date(2026, 10, 16))
    assert list(bm["date"]) == [date(2026, 11, 3)] and bool(bm["results"].iloc[0])


def test_ticket_preview_runs_the_real_gate():
    when = W.next_session_10am(date(2026, 10, 16))
    assert when.weekday() == 0 and when.hour == 10                 # Friday -> Monday
    ok = W.ticket(symbol="INFY", price=1500.0, shares=6, cost_one_side=0.0015, when=when)
    big = W.ticket(symbol="INFY", price=1500.0, shares=60, cost_one_side=0.0015, when=when)
    assert ok["verdict"] == "would pass" and "10,000" in big["verdict"]
