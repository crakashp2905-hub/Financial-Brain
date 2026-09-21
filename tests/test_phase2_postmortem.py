"""Learning from closed trades without fitting noise (C25).

The ask was "never repeat this mistake". With a few dozen trades, reinforcement learning
would fit noise and then trade on it. These tests pin the duller mechanism that works at
this sample size: count the cases, refuse to call two trades a lesson, and attach the
trades to every rule so it can be argued with.
"""
from __future__ import annotations


import pytest

from financial_brain.config import Config
from financial_brain.decisions import postmortem as pm
from financial_brain.storage.db import Database


@pytest.fixture
def con(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as c:
        yield c


def _pm(con, did, excess, *, regime="NEUTRAL", prompted_by="ORDER_WIN",
        bucket="liquid", action="BUY", fired=False):
    con.execute("""INSERT INTO decision_postmortems (decision_id, excess, regime,
                   prompted_by, bucket, action, invalidation_fired, features, created_at)
                   VALUES (?,?,?,?,?,?,?, '{}', NOW())""",
                [did, excess, regime, prompted_by, bucket, action, fired])


def test_two_bad_trades_are_not_a_lesson(con):
    _pm(con, "d1", -0.08, regime="RISK_OFF")
    _pm(con, "d2", -0.12, regime="RISK_OFF")
    for i in range(6):
        _pm(con, f"w{i}", 0.03)
    risk_off = next(x for x in pm.lessons(con) if x.feature == "regime=RISK_OFF")
    assert risk_off.n == 2 and risk_off.mean_excess < 0
    assert not risk_off.confirmed(), "two trades is a story, not a rule"
    assert pm.gate(con, regime="RISK_OFF") == []


def test_enough_cases_with_a_material_gap_becomes_a_rule(con):
    for i in range(6):
        _pm(con, f"r{i}", -0.06, regime="RISK_OFF")
    for i in range(6):
        _pm(con, f"n{i}", 0.04, regime="RISK_ON")
    risk_off = next(x for x in pm.lessons(con) if x.feature == "regime=RISK_OFF")
    assert risk_off.confirmed()
    assert "6 of 6 lost" in risk_off.describe()
    blocking = pm.gate(con, regime="RISK_OFF")
    assert [x.feature for x in blocking] == ["regime=RISK_OFF"]
    assert blocking[0].decisions[0].startswith("r")


def test_a_small_difference_is_not_worth_a_rule(con):
    """Six trades that did marginally worse are not a reason to forbid anything."""
    for i in range(6):
        _pm(con, f"a{i}", 0.010, prompted_by="RESULTS")
    for i in range(6):
        _pm(con, f"b{i}", 0.015, prompted_by="ORDER_WIN")
    results = next(x for x in pm.lessons(con) if x.feature == "prompted_by=RESULTS")
    assert results.n == 6 and results.gap < 0
    assert not results.confirmed(), "a 0.5% gap is noise"


def test_wins_are_recorded_too(con):
    """A rule learned from losses alone would also forbid the wins sharing its features."""
    for i in range(5):
        _pm(con, f"w{i}", 0.09, prompted_by="ORDER_WIN")
    for i in range(5):
        _pm(con, f"l{i}", -0.09, prompted_by="ORDER_WIN")
    order_win = next(x for x in pm.lessons(con) if x.feature == "prompted_by=ORDER_WIN")
    assert order_win.n == 10 and order_win.losses == 5
    assert not order_win.confirmed(), "half of them won"


def test_every_rule_carries_the_trades_behind_it(con):
    for i in range(5):
        _pm(con, f"x{i}", -0.07, bucket="illiquid")
    for i in range(5):
        _pm(con, f"y{i}", 0.05, bucket="liquid")
    lesson = next(x for x in pm.lessons(con) if x.feature == "liquidity=illiquid")
    assert sorted(lesson.decisions) == [f"x{i}" for i in range(5)]
    assert lesson.confirmed()


def test_features_at_entry_use_what_was_knowable_then(con):
    con.execute("""INSERT INTO market_regime (business_date, version, regime, raw_regime,
                   computed_at) VALUES (DATE '2026-03-01', 'v1', 'RISK_OFF', 'RISK_OFF',
                   NOW())""")
    con.execute("""INSERT INTO market_regime (business_date, version, regime, raw_regime,
                   computed_at) VALUES (DATE '2026-05-01', 'v1', 'RISK_ON', 'RISK_ON',
                   NOW())""")
    con.execute("""INSERT INTO announcements (news_id, source, business_date, event_type,
                   materiality, evidence_key, observed_at, isin, company, headline)
                   VALUES ('n1','BSE',DATE '2026-03-10','ORDER_WIN','high','k',NOW(),
                           'INE000A01001','Acme','x')""")
    con.execute("""INSERT INTO decisions (decision_id, isin, action, horizon_days,
                   world_state_version, content, author, created_at)
                   VALUES ('d1','INE000A01001','BUY',90,'ws','{"action":"BUY",
                   "invalidation_checks":[{"check":"x"}]}','agent',NOW())""")
    con.execute("""INSERT INTO paper_trades (decision_id, isin, lineage, action, direction,
                   entry_date, entry_price, due_date, cost, bucket, status, excess,
                   opened_at) VALUES ('d1','INE000A01001','L1','BUY',1,DATE '2026-03-15',
                   100.0, DATE '2026-06-13', 0.004, 'liquid', 'closed', -0.05, NOW())""")
    f = pm.features_at_entry(con, "d1")
    assert f["regime"] == "RISK_OFF", "the regime on the entry date, not today's"
    assert f["prompted_by"] == "ORDER_WIN"
    assert f["typed_checks"] == 1 and f["invalidation_fired"] is False


def test_a_confirmed_lesson_stops_the_next_such_trade(con, monkeypatch):
    """The whole point: a mistake the record supports is not repeated."""
    from financial_brain.decisions import promote
    for i in range(6):
        _pm(con, f"r{i}", -0.06, regime="RISK_OFF")
    for i in range(6):
        _pm(con, f"n{i}", 0.04, regime="RISK_ON")
    con.execute("""INSERT INTO market_regime (business_date, version, regime, raw_regime,
                   computed_at) VALUES (DATE '2026-09-18','v1','RISK_OFF','RISK_OFF',NOW())""")
    con.execute("""INSERT INTO announcements (news_id, source, business_date, event_type,
                   materiality, evidence_key, observed_at, isin, company, headline)
                   VALUES ('n1','BSE',DATE '2026-09-18','ORDER_WIN','high','k',NOW(),
                           'INE000A01001','Acme','x')""")
    con.execute("""INSERT INTO decisions (decision_id, isin, action, horizon_days,
                   world_state_version, content, author, created_at)
                   VALUES ('dnew','INE000A01001','BUY',90,'ws',
                   '{"action":"BUY","isin":"INE000A01001"}','agent:committee',NOW())""")
    assert [x.feature for x in promote.postmortem.gate(
        con, **promote._context(con, "dnew"))] == ["regime=RISK_OFF"]


def test_nothing_learned_yet_forbids_nothing(con):
    """The usual state, and it must not quietly block trades."""
    from financial_brain.decisions import promote
    _pm(con, "d1", -0.2, regime="RISK_OFF")
    con.execute("""INSERT INTO market_regime (business_date, version, regime, raw_regime,
                   computed_at) VALUES (DATE '2026-09-18','v1','RISK_OFF','RISK_OFF',NOW())""")
    con.execute("""INSERT INTO decisions (decision_id, isin, action, horizon_days,
                   world_state_version, content, author, created_at)
                   VALUES ('dnew','INE000A01001','BUY',90,'ws',
                   '{"action":"BUY","isin":"INE000A01001"}','agent:committee',NOW())""")
    assert promote.postmortem.gate(con, **promote._context(con, "dnew")) == []
