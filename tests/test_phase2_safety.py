"""Situational awareness as refusals (C20/C19).

Most ways this system could lose money are not wrong opinions but right opinions applied
in the wrong conditions. These tests pin each refusal, and - as importantly - that a
clean situation is *not* refused.
"""
from __future__ import annotations

from datetime import date

import pytest

from financial_brain.config import Config
from financial_brain.decisions import safety
from financial_brain.storage.db import Database

TODAY = date(2026, 9, 21)


@pytest.fixture
def con(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as c:
        c.execute("""CREATE OR REPLACE VIEW adjusted_prices AS SELECT * FROM (VALUES
            ('L1', DATE '2026-09-18', CAST(100.0 AS DOUBLE)))
            t(lineage, business_date, close_adj)""")
        c.execute("""CREATE OR REPLACE VIEW security_lineage AS
                     SELECT 'INE000A01001' AS isin, 'L1' AS lineage""")
        c.execute("""CREATE OR REPLACE TABLE features AS SELECT * FROM (VALUES
            ('L1', DATE '2026-09-18', CAST(5.0e7 AS DOUBLE)))
            t(lineage, business_date, adv20)""")
        c.execute("""INSERT INTO announcements (news_id, source, business_date, event_type,
                     materiality, evidence_key, observed_at, isin, company, headline)
                     VALUES ('n1','BSE',DATE '2026-09-18','ORDER_WIN','high','k',NOW(),
                             'INE000A01001','Acme','x')""")
        c.execute("""INSERT INTO market_regime (business_date, version, regime, raw_regime,
                     computed_at) VALUES (DATE '2026-09-18','v1','NEUTRAL','NEUTRAL',NOW())""")
        yield c


def test_a_clean_situation_is_not_refused(con):
    a = safety.assess(con, isin="INE000A01001", as_of=TODAY)
    assert a.safe, a.describe()
    assert a.describe() == "no safety condition breached"


def test_stale_prices_stop_everything(con):
    a = safety.assess(con, isin="INE000A01001", as_of=date(2026, 10, 20))
    assert not a.safe
    assert any(b.check == "stale_data" for b in a.breaches)


def test_a_crisis_regime_blocks_new_longs(con):
    con.execute("""INSERT INTO market_regime (business_date, version, regime, raw_regime,
                   computed_at) VALUES (DATE '2026-09-19','v1','CRISIS','CRISIS',NOW())""")
    a = safety.assess(con, isin="INE000A01001", as_of=TODAY)
    assert not a.safe and "CRISIS" in a.describe()


def test_a_name_too_thin_to_exit_is_refused(con):
    con.execute("""CREATE OR REPLACE TABLE features AS SELECT * FROM (VALUES
        ('L1', DATE '2026-09-18', CAST(2.0e6 AS DOUBLE)))
        t(lineage, business_date, adv20)""")
    a = safety.assess(con, isin="INE000A01001", as_of=TODAY)
    assert not a.safe and "below the" in a.describe()


def test_a_position_too_large_for_the_name_is_refused(con):
    """Being right is useless if the exit moves the price."""
    a = safety.assess(con, isin="INE000A01001", as_of=TODAY, position_inr=2.0e7)
    assert not a.safe and "of a day's traded value" in a.describe()


def test_too_many_open_positions_stops_new_ones(con):
    for i in range(safety.MAX_OPEN_POSITIONS):
        con.execute("""INSERT INTO paper_trades (decision_id, isin, lineage, action,
            direction, entry_date, entry_price, due_date, cost, status, opened_at)
            VALUES (?, ?, 'L1','BUY',1,DATE '2026-09-18',100.0,DATE '2026-12-17',0.004,
                    'open', NOW())""", [f"d{i}", f"INE{i:03d}Z01001"])
    a = safety.assess(con, isin="INE000A01001", as_of=TODAY)
    assert not a.safe and "positions already open" in a.describe()


def test_a_run_of_losses_pauses_new_trades(con):
    for i in range(safety.DRAWDOWN_WINDOW):
        con.execute("""INSERT INTO paper_trades (decision_id, isin, lineage, action,
            direction, entry_date, entry_price, due_date, cost, status, excess, exit_date,
            opened_at) VALUES (?, 'INE000A01001','L1','BUY',1,DATE '2026-06-01',100.0,
            DATE '2026-08-30',0.004,'closed', -0.12, DATE '2026-08-30', NOW())""",
                    [f"loss{i}"])
    a = safety.assess(con, isin="INE000A01001", as_of=TODAY)
    assert not a.safe and "new trades paused" in a.describe()


def test_a_few_losses_do_not_pause_anything(con):
    """The pause needs a window, not a bad afternoon."""
    con.execute("""INSERT INTO paper_trades (decision_id, isin, lineage, action, direction,
        entry_date, entry_price, due_date, cost, status, excess, exit_date, opened_at)
        VALUES ('l1','INE000A01001','L1','BUY',1,DATE '2026-06-01',100.0,
                DATE '2026-08-30',0.004,'closed',-0.30,DATE '2026-08-30',NOW())""")
    assert safety.assess(con, isin="INE000A01001", as_of=TODAY).safe


def test_every_breach_is_reported_not_just_the_first(con):
    """'Thin and in a crisis' is a different situation from either alone."""
    con.execute("""INSERT INTO market_regime (business_date, version, regime, raw_regime,
                   computed_at) VALUES (DATE '2026-09-19','v1','CRISIS','CRISIS',NOW())""")
    con.execute("""CREATE OR REPLACE TABLE features AS SELECT * FROM (VALUES
        ('L1', DATE '2026-09-18', CAST(1.0e6 AS DOUBLE)))
        t(lineage, business_date, adv20)""")
    a = safety.assess(con, isin="INE000A01001", as_of=TODAY)
    checks = {b.check for b in a.breaches}
    assert {"crisis", "liquidity"} <= checks


def test_the_market_window_answers_without_naming_a_company(con):
    w = safety.window(con, as_of=TODAY)
    assert w["clear"] is True and w["breaches"] == []
    con.execute("""INSERT INTO market_regime (business_date, version, regime, raw_regime,
                   computed_at) VALUES (DATE '2026-09-19','v1','CRISIS','CRISIS',NOW())""")
    assert safety.window(con, as_of=TODAY)["clear"] is False


def test_a_refusal_says_when_to_look_again(con):
    con.execute("""CREATE OR REPLACE VIEW adjusted_prices AS SELECT * FROM (VALUES
        ('L1', DATE '2026-09-18', CAST(100.0 AS DOUBLE)),
        ('L1', DATE '2026-09-22', CAST(101.0 AS DOUBLE)))
        t(lineage, business_date, close_adj)""")
    assert safety.next_review(con, as_of=TODAY) == date(2026, 9, 22)
