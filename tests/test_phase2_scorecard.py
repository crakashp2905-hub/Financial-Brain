"""Scoring the system's own calls (C25).

The point of this module is restraint: a system that scores itself generously is worse
than one that never scores itself at all, because it sounds like evidence.
"""
from __future__ import annotations

from datetime import date

import pytest

from financial_brain.config import Config
from financial_brain.evaluation import scorecard as sc
from financial_brain.storage.db import Database


@pytest.fixture
def con(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as c:
        yield c


def _trade(con, i, excess, *, action="BUY", bucket="liquid", status="closed"):
    con.execute("""INSERT INTO paper_trades (decision_id, isin, lineage, action,
        direction, entry_date, entry_price, due_date, cost, bucket, status, excess,
        opened_at) VALUES (?,?,?,?,1,DATE '2026-01-01',100.0,DATE '2026-04-01',0.004,
        ?,?,?,NOW())""",
                [f"dc_{i}", "INE000A01001", "L1", action, bucket, status, excess])


def test_no_trades_says_there_is_nothing_to_judge(con):
    card = sc.build(con)
    assert card.overall is None and not card.enough()
    assert "no decision it can be scored on" in " ".join(sc.lines(card))


def test_a_small_winning_streak_is_not_called_an_edge(con):
    """8 wins from 12 is 67%, and the interval still includes a coin."""
    for i in range(8):
        _trade(con, i, 0.03)
    for i in range(8, 12):
        _trade(con, i, -0.02)
    card = sc.build(con)
    assert card.overall.hit_rate == pytest.approx(8 / 12)
    assert not card.enough()
    text = " ".join(sc.lines(card))
    assert "not enough evidence yet (12 of 20 closed trades)" in text
    assert "too small to separate skill from luck" in text


def test_with_enough_trades_a_real_edge_is_stated_with_its_interval(con):
    for i in range(40):
        _trade(con, i, 0.04 if i % 5 else -0.02)      # 32 of 40 win
    card = sc.build(con)
    assert card.enough()
    low, high = card.overall.interval
    assert low > 0.5, "80% over 40 trades clears a coin"
    assert "better than a coin" in card.overall.verdict()


def test_a_losing_record_is_reported_as_such(con):
    for i in range(40):
        _trade(con, i, -0.03 if i % 5 else 0.01)      # 8 of 40 win
    card = sc.build(con)
    assert "worse than a coin" in card.overall.verdict()
    assert card.overall.mean_excess < 0


def test_an_indecisive_record_is_not_dressed_up(con):
    for i in range(40):
        _trade(con, i, 0.02 if i % 2 else -0.02)      # exactly half
    assert "indistinguishable from chance" in sc.build(con).overall.verdict()


def test_slices_separate_where_the_result_came_from(con):
    """An edge that exists only in illiquid names is usually a cost model's illusion."""
    for i in range(10):
        _trade(con, i, 0.05, bucket="illiquid")
    for i in range(10, 20):
        _trade(con, i, -0.01, bucket="liquid")
    card = sc.build(con)
    by = {s.name: s for s in card.by_bucket}
    assert by["illiquid"].hit_rate == 1.0 and by["liquid"].hit_rate == 0.0
    assert "by liquidity" in " ".join(sc.lines(card))


def test_open_trades_are_counted_but_never_scored(con):
    _trade(con, 1, None, status="open")
    _trade(con, 2, 0.05)
    card = sc.build(con)
    assert card.open_trades == 1 and card.overall.n == 1


def test_wilson_interval_behaves_at_the_edges():
    assert sc.wilson(0, 0) == (0.0, 1.0)
    low, high = sc.wilson(1, 1)
    assert low < 1.0 and high == 1.0, "one win is not certainty"
