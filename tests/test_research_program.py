"""New research diagnostics must remain descriptive and fail closed on weak inputs."""
from datetime import date, datetime, timedelta, timezone

import duckdb
import pytest

from financial_brain.evaluation import research_program as R

IST = timezone(timedelta(hours=5, minutes=30))


def test_m1_snapshot_is_descriptive_and_does_not_promote():
    con = duckdb.connect()
    con.execute("CREATE TABLE zoo_pnl (strategy VARCHAR, day DATE, gross DOUBLE, cost DOUBLE, net DOUBLE, bench DOUBLE)")
    con.execute("INSERT INTO zoo_pnl VALUES (?, ?, ?, ?, ?, ?)", [R.M1, date(2026, 10, 20), .02, .004, .016, .01])
    out = R.m1_forward_snapshot(con)
    assert out["sessions"] == 1 and out["mean_net_excess"] == pytest.approx(.006)
    assert out["status"] == "DESCRIPTIVE_ONLY_NO_NEW_VERDICT"


def test_filter_counterfactual_charges_missed_winners_as_well_as_avoided_losers():
    rows = [{"trade_id": "winner", "day": "2020-01-02", "baseline_net_inr": 100, "keep": False},
            {"trade_id": "loser", "day": "2020-01-03", "baseline_net_inr": -40, "keep": False}]
    out = R.filter_counterfactual(rows)
    assert out["baseline_net_inr"] == 60 and out["filtered_net_inr"] == 0
    assert out["difference_inr"] == -60
    assert out["avoided_losers"] == out["missed_winners"] == 1


def test_event_diagnostic_rejects_lookahead_and_counts_missed_peer_baseline():
    event = datetime(2020, 1, 2, 16, tzinfo=IST)
    row = {"event_id": "one", "event_at": event.isoformat(),
           "entry_at": (event + timedelta(days=1)).isoformat(),
           "exit_at": (event + timedelta(days=8)).isoformat(),
           "net_return": .01, "peer_net_return": .02}
    result = R.event_peer_diagnostic([row])
    assert result["mean_net_excess"] == pytest.approx(-.01)
    assert result["status"] == "TRAINING_DESCRIPTION_NOT_A_TRIAL"
    with pytest.raises(ValueError, match="follow publication"):
        R.event_peer_diagnostic([{**row, "entry_at": event.isoformat()}])
    with pytest.raises(ValueError, match="sealed training"):
        R.event_peer_diagnostic([{**row, "exit_at": "2022-01-01T10:00:00+05:30"}])


def _book(at, strike, kind="CE", bid=10, ask=11, qty=100):
    return R.Book(at, date(2020, 1, 30), strike, kind, bid, ask, qty, qty)


def test_vertical_prices_both_legs_and_complete_fee_input():
    entry = datetime(2020, 1, 2, 10, tzinfo=IST)
    exit_ = entry + timedelta(days=1)
    out = R.vertical_roundtrip(
        atm_entry=_book(entry, 100, bid=10, ask=11),
        wing_entry=_book(entry, 110, bid=4, ask=5),
        atm_exit=_book(exit_, 100, bid=14, ask=15),
        wing_exit=_book(exit_, 110, bid=6, ask=7),
        entry_at=entry, exit_at=exit_, lots=1, lot_size=50,
        structure="debit", fees_inr=20)
    assert out["entry_points"] == 7 and out["exit_points"] == 7
    assert out["net_pnl_inr"] == -20
    assert "no validated options edge" in out["evidence"]


def test_vertical_refuses_unsynchronised_or_unfillable_books():
    entry = datetime(2020, 1, 2, 10, tzinfo=IST)
    exit_ = entry + timedelta(days=1)
    inputs = dict(atm_entry=_book(entry, 100), wing_entry=_book(entry, 110, bid=4, ask=5),
                  atm_exit=_book(exit_, 100), wing_exit=_book(exit_, 110, bid=4, ask=5),
                  entry_at=entry, exit_at=exit_, lots=1, lot_size=50,
                  structure="debit", fees_inr=0)
    with pytest.raises(ValueError, match="simultaneously"):
        R.vertical_roundtrip(**{**inputs, "wing_entry": _book(entry - timedelta(seconds=3), 110, bid=4, ask=5)})
    with pytest.raises(ValueError, match="too small"):
        R.vertical_roundtrip(**{**inputs, "wing_entry": _book(entry, 110, bid=4, ask=5, qty=5)})


def test_training_coverage_refuses_holdout():
    with pytest.raises(ValueError, match="training period"):
        R.filing_timestamp_coverage(duckdb.connect(), end=date(2022, 1, 1))
