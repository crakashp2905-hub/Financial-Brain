"""Phase 1 - world state and daily brief: cited, point-in-time, reproducible."""
from __future__ import annotations

import re
from datetime import date, datetime

import pytest

from financial_brain.config import Config
from financial_brain.events.classify import classify, legal_tone
from financial_brain.storage.db import Database

D0, D1, D2 = date(2026, 9, 17), date(2026, 9, 18), date(2026, 9, 21)   # Thu, Fri, Mon


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(data_root=tmp_path).ensure()


@pytest.fixture
def db(cfg) -> Database:
    d = Database(cfg)
    d.migrate()
    return d


def _world(con):
    for d, c in ((D0, 100.0), (D1, 89.0), (D2, 90.0)):          # TATACHEM falls 11% on D1
        con.execute("""INSERT INTO universe_snapshots (business_date, isin, exchange, ticker,
            series, instrument_type, turnover, close_price, tradable)
            VALUES (?, 'INE092A01019', 'NSE', 'TATACHEM', 'EQ', 'STK', 5e9, ?, TRUE)""", [d, c])
    for d, lvl in ((D0, 23000.0), (D1, 23100.0)):
        con.execute("""INSERT INTO index_levels (business_date, index_name, close_level,
            variant, source, observed_at) VALUES (?, 'Nifty 50', ?, 'PRICE', 'NSE', NOW())""",
                    [d, lvl])
    con.execute("""INSERT INTO market_regime (business_date, version, regime, raw_regime,
        reasons, breadth_200, advances, declines, vix, drawdown, computed_at)
        VALUES (?, 'v2', 'RISK_OFF', 'RISK_OFF', 'Nifty 50 below a falling 200-session average',
                0.47, 1774, 839, 11.4, -0.11, NOW())""", [D1])
    rows = [  # (id, published, type, materiality, headline)
        ("in-window", datetime(2026, 9, 18, 16, 5), "RESULTS", "high",
         "Q1 results: profit down 40% on soda-ash prices"),
        ("before-window", datetime(2026, 9, 18, 8, 0), "ORDER_WIN", "high",
         "Order win published before the window opened"),
        ("after-as-of", datetime(2026, 9, 21, 9, 30), "CREDIT_RATING", "high",
         "Rating downgrade published after the brief's as-of time"),
        ("short", datetime(2026, 9, 18, 17, 0), "LEGAL_REGULATORY", "high", "Enclosed"),
    ]
    for nid, pub, kind, mat, head in rows:
        con.execute("""INSERT INTO announcements (news_id, source, business_date, isin,
            company, headline, subject, event_type, materiality, published_at,
            evidence_key, observed_at)
            VALUES (?, 'BSE', ?, 'INE092A01019', 'Tata Chemicals Ltd', ?, ?, ?, ?, ?, 'k', NOW())""",
                    [nid, pub.date(), head,
                     "Tata Chemicals Ltd - 500770 - Favourable Order received from ITAT",
                     kind, mat, pub])


def test_world_state_is_point_in_time_and_reproducible(db):
    from financial_brain.worldstate import build as ws
    with db.connect() as con:
        _world(con)
        s = ws.build(con, D1)
        again = ws.build(con, D1)
    ids = {e["news_id"] for e in s["events"]}
    assert s["as_of"] == datetime(2026, 9, 21, 9, 0), "next session's pre-open"
    assert "after-as-of" not in ids, "nothing published after as_of may enter"
    assert "before-window" not in ids
    assert again["version_id"] == s["version_id"], "content-addressed"


def test_uninformative_headline_falls_back_to_the_subject(db):
    from financial_brain.worldstate import build as ws
    with db.connect() as con:
        _world(con)
        s = ws.build(con, D1)
    short = next(e for e in s["events"] if e["news_id"] == "short")
    assert "ITAT" in short["text"] and short["tone"] == "favourable"


def test_brief_cites_every_claim_and_explains_the_mover(cfg, db):
    from financial_brain.brief.render import render
    from financial_brain.worldstate import build as ws
    with db.connect() as con:
        _world(con)
        md, cited = render(con, ws.build(con, D1))
    used = {int(n) for n in re.findall(r"\[(\d+)\]", md.split("## Sources")[0])}
    assert used == set(range(1, len(cited) + 1)), "every citation number resolves"
    mover = next(ln for ln in md.splitlines() if ln.startswith("- **TATACHEM** -11.00%"))
    assert "filed:" in mover and "Results" in mover, "why it moved, on the same line"
    # the same filing is the same evidence wherever it is cited
    assert md.count("Q1 results: profit down 40%") == 2
    refs = re.findall(r"Q1 results: profit down 40% on soda-ash prices \[(\d+)\]", md)
    assert len(set(refs)) == 1, refs
    assert "Rating downgrade" not in md
    assert "_(favourable)_" in md


def test_tds_on_a_dividend_is_not_a_legal_order():
    kind, *_ = classify("Company Update", "General",
                        "Communication to shareholders on TDS on Interim Dividend")
    assert kind != "LEGAL_REGULATORY"


@pytest.mark.parametrize("text, tone", [
    ("Office of Superintendent CGST has dropped all the proceedings", "favourable"),
    ("Favourable Order received from the Income Tax Appellate Tribunal", "favourable"),
    ("Receipt of GST demand order and penalty", None),
])
def test_legal_tone(text, tone):
    assert legal_tone(text) == tone


def test_a_red_flag_names_the_promoter_group(db):
    """Group contagion: a pledge at one Tata company shows its listed siblings, cited."""
    from financial_brain.brief import render
    from financial_brain.worldstate import build as ws
    with db.connect() as con:
        _world(con)
        con.execute("""INSERT INTO announcements (news_id, source, business_date, isin, company,
            headline, event_type, materiality, published_at, evidence_key, observed_at)
            VALUES ('pledge', 'BSE', ?, 'INE092A01019', 'Tata Chemicals Ltd',
            'Disclosure of encumbrance by promoter', 'PROMOTER_PLEDGE', 'high', ?, 'k', NOW())""",
                    [D1, datetime(2026, 9, 18, 18, 0)])
        for i, (scrip, isin, co) in enumerate([("500770", "INE092A01019", "Tata Chemicals Ltd"),
                                               ("500470", "INE081A01020", "Tata Steel Ltd")] * 2):
            con.execute("""INSERT INTO holder_filings VALUES (?, DATE '2025-01-01', NULL, ?, ?, ?,
                           'PLEDGE', 'Tata Sons Pvt Ltd', 'TATA SONS PVT LTD', 'organisation')""",
                        [f"h{i}", scrip, isin, co])
        s = ws.build(con, D1)
        text, _ = render.render(con, s)
    pledge = next(e for e in s["events"] if e["news_id"] == "pledge")
    assert pledge["group"]["anchor"] == "Tata Sons Pvt Ltd"
    assert pledge["group"]["siblings"] == ["Tata Steel Ltd"]
    assert re.search(r"group: Tata Sons Pvt Ltd, 2 listed — also Tata Steel Ltd \[\d+\]", text)


def test_model_tone_is_cited_only_when_the_model_cleared_its_bar(db, monkeypatch):
    """ADR-0002: an accepted model reading is shown and cited as MODEL-tier evidence;
    an unaccepted one is kept in the table but never reaches the brief."""
    from financial_brain.brief import render
    from financial_brain.events import tone
    from financial_brain.evaluation import models as bench
    from financial_brain.llm import system1
    from financial_brain.worldstate import build as ws
    answers = {"in-window": ("negative", 0.99), "short": ("negative", 0.4),
               "before-window": ("neutral", 0.95)}
    monkeypatch.setattr(bench, "decider", lambda task, m: (lambda row: system1.Decision(
        label=answers[row["nid"]][0], probs={}, confidence=answers[row["nid"]][1],
        model="stub")))
    real_decide = tone.router.decide
    with db.connect() as con:
        _world(con)
        rows = {r[1]: r[0] for r in con.execute(
            "SELECT news_id, headline FROM announcements").fetchall()}
        monkeypatch.setattr(tone.router, "decide", lambda c, task, row, steps: real_decide(
            c, task, {**row, "nid": rows.get(row["state"], "short")}, steps=steps))
        monkeypatch.setattr(tone.router, "plan", lambda c, task: [
            {"model": "stub", "threshold": 0.9, "tier": 1}])
        got = tone.classify_day(con, D1)
        s = ws.build(con, D1)
        text, _ = render.render(con, s)
    assert got == {"classified": 3, "accepted": 2, "refreshed": 0, "on_news_headline": 0}
    marked = {e["news_id"]: e.get("model_tone") for e in s["events"]}
    assert marked["in-window"]["tone"] == "negative" and marked["short"] is None
    assert re.search(r"_\(adverse per stub\)_ \[\d+\]", text)
