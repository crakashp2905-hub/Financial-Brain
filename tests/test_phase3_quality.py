"""Decision quality, scored apart from profit and loss (C25).

A good decision can lose money and a bad one can make it. A system that learns only from
P&L learns the wrong lesson about as often as the right one.
"""
from __future__ import annotations

import json

import pytest

from financial_brain.config import Config
from financial_brain.decisions import quality
from financial_brain.storage.db import Database

GOOD = {
    "isin": "INE000A01001", "action": "BUY",
    "thesis": "margin expansion from the new line, already visible in two quarters",
    "primary_uncertainty": "input costs if crude runs",
    "supporting_evidence": ["ev_a", "ev_b", "ev_c"],
    "contrary_evidence": ["ev_d", "ev_e"],
    "invalidation_conditions": ["falls 15% from entry", "margin below 18%"],
    "invalidation_checks": [{"check": "drawdown_from", "reference": 1000.0, "pct": 0.15},
                            {"check": "adverse_tone"}],
    "scenarios": {"bull": {"probability": 0.3, "return": 0.30},
                  "base": {"probability": 0.5, "return": 0.08},
                  "bear": {"probability": 0.2, "return": -0.12}},
    "sizing": {"entry": 1000.0, "invalidation": 900.0, "risk_budget": 0.005},
}


@pytest.fixture
def con(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as c:
        for eid in ("ev_a", "ev_b", "ev_c", "ev_d", "ev_e"):
            c.execute("""INSERT INTO evidence (evidence_id, kind, subject, as_of, claim,
                         value, source, source_tier, derivation, confidence, quality,
                         observed_at) VALUES (?, 'x','INE000A01001',NOW(),'c','{}','BSE',
                         1,'d','high','ok',NOW())""", [eid])
        yield c


def _decide(con, did, content):
    con.execute("""INSERT INTO decisions (decision_id, isin, action, horizon_days,
                   world_state_version, content, author, created_at)
                   VALUES (?, ?, ?, 90, 'ws', ?, 'agent:test', NOW())""",
                [did, content["isin"], content["action"], json.dumps(content)])
    con.execute("""INSERT INTO decision_events (decision_id, seq, to_state, actor, note,
                   event_at) VALUES (?, 1, 'DRAFT', 'agent:test', 'drafted', NOW())""",
                [did])


def test_a_well_made_decision_scores_well(con):
    _decide(con, "d1", GOOD)
    s = quality.assess_decision(con, "d1")
    assert s.score >= 0.85 and s.grade() == "sound"


def test_one_sided_evidence_is_penalised(con):
    content = {**GOOD, "contrary_evidence": []}
    _decide(con, "d2", content)
    s = quality.assess_decision(con, "d2")
    assert s.parts["evidence"] == 0.0
    assert any("one side" in n for n in s.notes)


def test_prose_invalidation_with_nothing_checking_it_scores_low(con):
    content = {**GOOD, "invalidation_checks": []}
    _decide(con, "d3", content)
    s = quality.assess_decision(con, "d3")
    assert s.parts["falsifiability"] == pytest.approx(0.3)
    assert any("prose only" in n for n in s.notes)


def test_a_negative_expected_value_is_marked_down_not_hidden(con):
    content = {**GOOD, "scenarios": {"bull": {"probability": 0.2, "return": 0.10},
                                     "bear": {"probability": 0.8, "return": -0.10}}}
    _decide(con, "d4", content)
    s = quality.assess_decision(con, "d4")
    assert s.parts["arithmetic"] == pytest.approx(0.4)
    assert any("expected value is -6.0%" in n for n in s.notes)


def test_a_weight_with_no_invalidation_behind_it_is_not_sizing(con):
    content = {**GOOD, "sizing": {"weight": 0.03}}
    _decide(con, "d5", content)
    s = quality.assess_decision(con, "d5")
    assert s.parts["sizing"] == pytest.approx(0.3)


def test_quality_is_independent_of_the_outcome(con):
    """The same content scores the same whether the trade won or lost."""
    _decide(con, "win", GOOD)
    _decide(con, "loss", {**GOOD, "isin": "INE111B01001"})
    for did, excess in (("win", 0.18), ("loss", -0.18)):
        con.execute("""INSERT INTO paper_trades (decision_id, isin, lineage, action,
            direction, entry_date, entry_price, due_date, cost, status, excess, opened_at)
            VALUES (?, 'INE000A01001','L1','BUY',1,DATE '2026-01-01',100.0,
                    DATE '2026-04-01',0.004,'closed',?,NOW())""", [did, excess])
    a = quality.assess_decision(con, "win").score
    b = quality.assess_decision(con, "loss").score
    assert a == b, "process quality cannot depend on what the market did"


def test_the_comparison_says_whether_process_or_edge_is_missing(con):
    _decide(con, "good", GOOD)
    _decide(con, "sloppy", {**GOOD, "contrary_evidence": [], "invalidation_checks": [],
                            "sizing": {"weight": 0.05}})
    for did, excess in (("good", -0.05), ("sloppy", 0.05)):
        con.execute("""INSERT INTO paper_trades (decision_id, isin, lineage, action,
            direction, entry_date, entry_price, due_date, cost, status, excess, opened_at)
            VALUES (?, 'INE000A01001','L1','BUY',1,DATE '2026-01-01',100.0,
                    DATE '2026-04-01',0.004,'closed',?,NOW())""", [did, excess])
    out = quality.against_outcomes(con)
    assert out["n"] == 2
    assert out["well_made"]["n"] == 1 and out["poorly_made"]["n"] == 1
    # Well-made losing and badly-made winning: the edge is absent, the process is not
    # the problem, and a P&L-only learner would draw exactly the wrong conclusion.
    assert out["quality_premium"] < 0
