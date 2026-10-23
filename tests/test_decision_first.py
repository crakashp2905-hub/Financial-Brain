"""The first screen must not promote a research ranking to a trade."""
import pandas as pd

from financial_brain.paper.terminal import decision_first


def test_first_screen_keeps_unconfirmed_candidates_as_research():
    best = pd.DataFrame([{"ticker": "ABC"}, {"ticker": "XYZ"}])
    html = decision_first(best, asof="2026-10-22", stale=[], kill_level="OK", my=None)
    assert "NO VALIDATED TRADE SIGNAL" in html
    assert "Top research watches" in html
    assert "ABC" in html and "XYZ" in html
    assert "no buy instruction" in html
    assert "not calibrated" in html


def test_first_screen_prioritizes_stale_data_and_holding_review():
    book = {"holdings": [{"symbol": "HOLD1", "exit": {"action": "REDUCE"}}]}
    html = decision_first(pd.DataFrame(), asof="2026-10-22", stale=["prices"],
                          kill_level="HALT", my=book)
    assert "DATA / RISK HOLD" in html
    assert "HOLD1" in html
    assert "A review is not an automatic sell order" in html
