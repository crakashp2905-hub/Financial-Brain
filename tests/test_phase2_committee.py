"""Investment committee: facts from the dossier only, cited debate, deterministic chair."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from financial_brain.committee import run as committee
from financial_brain.config import Config
from financial_brain.llm import backends, system1
from financial_brain.storage.db import Database

ISIN = "INE092A01019"


@pytest.fixture
def con(tmp_path):
    d = Database(Config(data_root=tmp_path).ensure())
    d.migrate()
    with d.connect() as c:
        for i in range(260):
            day = date(2025, 6, 1) + timedelta(days=i)
            c.execute("""INSERT INTO universe_snapshots (business_date, isin, exchange, ticker,
                series, instrument_type, turnover, close_price, tradable)
                VALUES (?, ?, 'NSE', 'TATACHEM', 'EQ', 'STK', 2e9, ?, TRUE)""",
                      [day, ISIN, 100 + i * 0.2])
        c.execute("""INSERT INTO announcements (news_id, source, business_date, isin, company,
            headline, event_type, materiality, published_at, evidence_key, observed_at)
            VALUES ('n1', 'BSE', DATE '2026-02-10', ?, 'Tata Chemicals Ltd',
            'Receipt of order worth Rs 500 crore from Indian Railways for soda ash supply',
            'ORDER_WIN', 'high', TIMESTAMP '2026-02-10 18:00:00', 'k', NOW())""", [ISIN])
        from financial_brain.features import indicators
        indicators.build(c)
        c.execute("""INSERT INTO world_states VALUES ('ws', DATE '2026-02-15', NOW(), ?, 0, 't')""",
                  [json.dumps({"as_of": "2026-02-16T09:00:00",
                               "market": {"regime": "NEUTRAL", "reasons": "calm"}})])
        yield c


def stances(bull=True):
    def decide(model, instruction, facts, choices):
        return system1.Decision(label="bullish" if bull else "bearish", probs={},
                                confidence=0.9, model=model)
    return decide


def debater(con):
    def generate(model, system, prompt, schema=None, max_tokens=0):
        ids = [line[1:line.index("]")] for line in prompt.splitlines() if line.startswith("[")]
        return backends.Completion(text="", model=model, parsed={"points": [
            {"claim": "order book strengthening", "fact_ids": [ids[0]]},
            {"claim": "made-up fact", "fact_ids": ["ev_invented"]}]})
    return generate


def test_debate_cannot_cite_what_the_dossier_does_not_hold(con):
    r = committee.convene(con, ISIN, "ws", constitution={}, decide=stances(),
                          generate=debater(con))
    assert r.dropped_points == 2, "one hallucinated citation from each side"
    assert all(p["fact_ids"] for p in r.bull + r.bear)


def test_bullish_committee_drafts_a_buy_with_cited_both_sides(con):
    r = committee.convene(con, ISIN, "ws", constitution={}, decide=stances(),
                          generate=debater(con))
    assert r.action == "BUY" and r.decision_id
    c = json.loads(con.execute("SELECT content FROM decisions WHERE decision_id = ?",
                               [r.decision_id]).fetchone()[0])
    known = {e for (e,) in con.execute("SELECT evidence_id FROM evidence").fetchall()}
    assert c["author"] == "agent:committee"
    assert set(c["supporting_evidence"]) <= known and set(c["contrary_evidence"]) <= known
    assert con.execute("SELECT COUNT(*) FROM committee_runs").fetchone()[0] == 1


def test_no_buy_when_the_constitution_would_refuse(con):
    book = {"exclusions": {"isins": [ISIN]}}
    r = committee.convene(con, ISIN, "ws", constitution=book, decide=stances(),
                          generate=debater(con))
    assert r.action == "WATCH", "bullish analysts cannot override the constitution"


def test_bearish_committee_avoids(con):
    r = committee.convene(con, ISIN, "ws", constitution={}, decide=stances(bull=False),
                          generate=debater(con))
    assert r.action == "AVOID"
