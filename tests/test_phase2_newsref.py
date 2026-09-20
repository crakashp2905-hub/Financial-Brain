"""News a filing refers to, recovered from the filing's own text (Tier 0, no fetching).

An exchange clarification reads as procedural boilerplate while the news it is about is
what moved the price. These tests pin the extraction rules and the fact that tone then
classifies the better text.
"""
from __future__ import annotations

import pytest

from financial_brain.config import Config
from financial_brain.events import newsref
from financial_brain.storage.db import Database


@pytest.fixture
def con(tmp_path):
    d = Database(Config(data_root=tmp_path).ensure())
    d.migrate()
    with d.connect() as c:
        yield c


def test_quoted_headline_wins_over_a_bare_link():
    t = ("The Exchange has sought clarification from Bandhan Bank Ltd with reference to "
         "news appeared in https://www.moneycontrol.com/ dated 08th September, 2026 "
         "quoting Bandhan Bank shares rise up to 4% as lender unveils four new credit "
         "cards.<BR><BR>The reply is awaited.")
    r = newsref.extract(t)
    assert r["how"] == "quoted"
    assert r["headline"] == "Bandhan Bank shares rise up to 4% as lender unveils four new credit cards"
    assert r["domain"] == "moneycontrol.com"


def test_the_article_link_is_preferred_over_the_publisher_home_page():
    """A filing usually carries both; only one of them says what happened."""
    t = ("news appeared in https://www.moneycontrol.com dated September 18, 2026 (Link: "
         "https://www.moneycontrol.com/news/business/markets/fssai-initiates-legal-action-"
         "against-nestle-india-on-baby-formula-shares-fall-nearly-2-14032.html)")
    r = newsref.extract(t)
    assert r["how"] == "url_slug"
    assert r["headline"].startswith("Fssai initiates legal action against nestle india")
    assert "fssai" in r["url"]


def test_publisher_named_without_a_scheme_is_still_identified():
    t = ("Clarification on news items appearing on the website 'www.moneycontrol.com' "
         "dated September 7, 2026 captioned 'PVR Inox shares fall 8% amid internal probe "
         "into alleged Rs 200-crore kickbacks'.")
    r = newsref.extract(t)
    assert r["domain"] == "moneycontrol.com" and r["url"] is None
    assert r["headline"].startswith("PVR Inox shares fall 8%")


def test_a_bare_link_yields_no_headline_and_says_so():
    r = newsref.extract('Clarification on the news item appearing in '
                        '"https://www.moneycontrol.com" dated 18th September 2026')
    assert r["how"] == "link_only" and r["headline"] is None


def test_an_exchange_url_is_not_external_news():
    assert newsref.extract("Attached is the filing at https://www.bseindia.com/xml/123.pdf") is None
    assert newsref.extract("Audited Financial Results for the quarter ended March 31, 2023") is None


def test_extract_day_stores_one_row_per_filing_with_news(con):
    con.execute("""INSERT INTO announcements (news_id, source, business_date, event_type,
                   materiality, evidence_key, observed_at, headline, subject) VALUES
                   ('n1','BSE',DATE '2026-09-18','GENERAL','high','k1',NOW(),
                    'clarification quoting Acme shares fall 9% on tax demand order',''),
                   ('n2','BSE',DATE '2026-09-18','RESULTS','low','k2',NOW(),
                    'Audited Financial Results','')""")
    assert newsref.extract_day(con, "2026-09-18") == {"scanned": 2, "with_news_reference": 1}
    rows = con.execute("SELECT news_id, headline, how FROM announcement_news").fetchall()
    assert rows == [("n1", "Acme shares fall 9% on tax demand order", "quoted")]
    # Re-running is a no-op, not a duplicate.
    assert newsref.extract_day(con, "2026-09-18")["scanned"] == 1


def test_tone_spends_its_daily_budget_on_the_newest_filings(con, monkeypatch):
    """The cap is a budget. A heavy day must not spend it on filings that belong to
    yesterday's brief: the window a brief covers ends at the next session's open, so the
    latest filings are the ones a reader will see."""
    from financial_brain.events import tone
    from financial_brain.llm import router, system1
    for i in range(4):
        con.execute("""INSERT INTO announcements (news_id, source, business_date, event_type,
                       materiality, evidence_key, observed_at, headline, published_at)
                       VALUES (?, 'BSE', DATE '2026-09-18', 'GENERAL', 'high', ?, NOW(),
                               ?, ?)""",
                    [f"n{i}", f"k{i}", f"filing number {i} with enough text to classify",
                     f"2026-09-18 {9 + i:02d}:00:00"])
    seen = []

    def fake_decide(con_, task, row, *, steps=None):
        seen.append(row["state"])
        d = system1.Decision(label="neutral", probs={}, confidence=0.99, model="m")
        return router.Routed(d, True, [])

    monkeypatch.setattr(tone.router, "route_is_current", lambda c, t: (True, "current"))
    monkeypatch.setattr(tone.router, "plan", lambda c, t: [{"model": "m", "tier": 1}])
    monkeypatch.setattr(tone.router, "has_route", lambda c, t: False)
    monkeypatch.setattr(tone.router, "decide", fake_decide)
    tone.classify_day(con, "2026-09-18", limit=2)
    assert [s.split()[2] for s in seen] == ["3", "2"], "newest two, not oldest two"
