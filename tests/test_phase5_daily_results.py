"""The 18:30 report: live trades as evidence, a replay shown apart, the run's status stated."""
from datetime import datetime

from financial_brain.paper import daily_results as DR
from financial_brain.paper import intraday as I
from financial_brain.paper import intraday_runner as R


def test_the_report_separates_live_from_replay_and_states_the_run(tmp_path):
    con = R.ledger(tmp_path / "l.duckdb")
    R.record_run(con, "2026-10-07", "live", started="2026-10-07T09:05:00", finished="2026-10-07T15:30:00",
                 status="complete", symbols=200, polls=1000)
    for mode in ("live", "replay"):
        e = I.DayEngine("2026-10-07")
        e.add_symbol("ABC", 100.0)
        e.set_opening("ABC", open_px=100.0, or_hi=101.0, or_lo=99.0)
        for t in e.on_price("ABC", datetime(2026, 10, 7, 9, 35), 101.5):
            R.save(con, t, mode)
        for t in e.end_of_day(datetime(2026, 10, 7, 15, 20), {"ABC": 103.0}):
            R.save(con, t, mode)
    text = DR.build(con, "2026-10-07")
    assert "live run:** complete, 1000 polls" in text
    assert "LIVE (the evidence)" in text and "REPLAY" in text and "not evidence" in text
    assert "ORB" in text and "Paper only" in text
    assert DR.build(con, "2026-10-08").count("No run recorded today") == 1
