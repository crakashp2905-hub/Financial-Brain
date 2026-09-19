"""P2-5 Investment Constitution: machine-checkable rules block a decision at risk review."""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from financial_brain.config import Config
from financial_brain.constitution import rules
from financial_brain.decisions import record as dr
from financial_brain.evidence import ledger
from financial_brain.storage.db import Database

ISIN = "INE092A01019"
BOOK = {"position": {"max_weight": 0.05}, "liquidity": {"min_adv20_inr": 5e7},
        "exclusions": {"isins": []},
        "governance": {"max_pledge_filings": 1, "pledge_lookback_days": 365,
                       "auditor_resignation_days": 365, "exclude_insolvency": True},
        "regime": {"no_new_buys_in": ["CRISIS"]}, "process": {"min_horizon_days": 20}}


@pytest.fixture
def con(tmp_path):
    d = Database(Config(data_root=tmp_path).ensure())
    d.migrate()
    with d.connect() as c:
        yield c


def _world(con, regime="NEUTRAL"):
    con.execute("""INSERT INTO world_states VALUES ('ws', DATE '2026-09-18', NOW(), ?, 0, 't')""",
                [json.dumps({"as_of": "2026-09-21T09:00:00", "market": {"regime": regime}})])


def _liquid(con, adv):
    con.execute("CREATE TABLE features (lineage VARCHAR, business_date DATE, adv20 DOUBLE)")
    con.execute("CREATE TABLE security_lineage (isin VARCHAR, lineage VARCHAR)")
    con.execute("INSERT INTO security_lineage VALUES (?, ?)", [ISIN, ISIN])
    con.execute("INSERT INTO features VALUES (?, DATE '2026-09-18', ?)", [ISIN, adv])


def _ann(con, n, day, event_type, published=None):
    con.execute("""INSERT INTO announcements (news_id, source, business_date, isin, event_type,
        materiality, published_at, evidence_key, observed_at)
        VALUES (?, 'BSE', ?, ?, ?, 'high', ?, 'k', NOW())""",
                [n, day, ISIN, event_type, published or datetime.combine(day, datetime.min.time())])


def _decision(action="BUY", weight=0.03, horizon=90):
    return {"isin": ISIN, "action": action, "horizon_days": horizon,
            "world_state_version": "ws", "sizing": {"weight": weight}}


def test_a_clean_buy_passes(con):
    _world(con)
    _liquid(con, 2e8)
    assert rules.check(con, _decision(), BOOK) == []


def test_each_rule_names_itself_and_its_facts(con):
    _world(con, regime="CRISIS")
    _liquid(con, 1e7)
    _ann(con, "a1", date(2026, 3, 1), "AUDITOR_RESIGNATION")
    _ann(con, "i1", date(2026, 6, 1), "INSOLVENCY")
    for i in range(2):
        con.execute("""INSERT INTO holder_filings VALUES (?, DATE '2026-05-01', NULL, '1', ?,
                       'X', 'PLEDGE', 'P', 'P', 'organisation')""", [f"p{i}", ISIN])
    broken = {v["rule"]: v["fact"] for v in
              rules.check(con, _decision(weight=0.08, horizon=5), BOOK)}
    assert set(broken) == {"position.max_weight", "process.min_horizon_days",
                           "regime.no_new_buys_in", "liquidity.min_adv20_inr",
                           "governance.max_pledge_filings",
                           "governance.auditor_resignation_days",
                           "governance.exclude_insolvency"}
    assert "CRISIS" in broken["regime.no_new_buys_in"]
    assert "2 promoter pledge filings" in broken["governance.max_pledge_filings"]


def test_unknown_liquidity_fails_closed(con):
    _world(con)
    assert [v["rule"] for v in rules.check(con, _decision(), BOOK)] == \
        ["liquidity.min_adv20_inr"]


def test_facts_published_after_the_world_state_do_not_count(con):
    _world(con)
    _liquid(con, 2e8)
    _ann(con, "late", date(2026, 9, 18), "AUDITOR_RESIGNATION",
         published=datetime(2026, 9, 22, 10, 0))
    assert rules.check(con, _decision(), BOOK) == []


def test_non_buys_skip_new_money_rules(con):
    _world(con, regime="CRISIS")
    assert rules.check(con, _decision(action="REDUCE"), BOOK) == []


def test_risk_review_is_blocked_by_the_constitution(con):
    _world(con)
    ev = [ledger.mint(con, kind="announcement", subject=ISIN, as_of=datetime(2026, 9, 18, 16),
                      published_at=datetime(2026, 9, 18, 16), claim=c, value={"c": c},
                      source="BSE", source_tier=1, derivation="test") for c in ("up", "down")]
    did = dr.draft(con, dr.Decision(
        isin=ISIN, action="BUY", horizon_days=90, thesis="t", world_state_version="ws",
        supporting_evidence=[ev[0]], contrary_evidence=[ev[1]], primary_uncertainty="u",
        invalidation_conditions=["x"], sizing={"weight": 0.03}))
    dr.advance(con, did, actor="agent:committee", constitution=BOOK)
    with pytest.raises(dr.DecisionError, match="liquidity.min_adv20_inr"):
        dr.advance(con, did, actor="agent:committee", constitution=BOOK)


def test_example_constitution_loads_and_says_it_is_an_example(tmp_path):
    book = rules.load(tmp_path)
    assert book["_is_example"] and book["position"]["max_weight"] == 0.05
    (tmp_path / "constitution.toml").write_text("[position]\nmax_weight = 0.1\n")
    assert rules.load(tmp_path)["position"]["max_weight"] == 0.1
