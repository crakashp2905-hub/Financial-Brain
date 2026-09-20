"""Public sources added in Phase 2: AMFI NAVs, and fetching a filing's linked article.

The fetch tests are offline on purpose - what needs pinning is the *politeness* logic
(allowlist, robots with wildcards, what counts as an article), not the network.
"""
from __future__ import annotations

from datetime import date

import pytest

from financial_brain.config import Config
from financial_brain.ingest.newsfetch import is_article_url
from financial_brain.providers import amfi
from financial_brain.providers.news_article import NewsArticle, Robots, parse_article
from financial_brain.storage.db import Database

NAV_FILE = b"""Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date

Open Ended Schemes(Equity Scheme - Large Cap Fund)

Axis Mutual Fund

135762;INF846K01WO1;-;Axis Bluechip Fund;Direct Plan;Growth Option;29.8856;18-Sep-2026
135763;INF846K01WP8;INF846K01WQ6;Axis Bluechip Fund;Regular Plan;IDCW Option;27.5300;18-Sep-2026
999999;-;-;Dormant Scheme;Direct Plan;Growth Option;N.A.;18-Sep-2026

SBI Mutual Fund

120503;INF200K01VT2;-;SBI Small Cap Fund;Direct Plan;Growth Option;180.4412;02-Jul-2018
"""


def test_nav_parse_carries_house_and_type_down_the_file():
    rows = amfi.parse(NAV_FILE)
    assert [r["scheme_code"] for r in rows] == ["135762", "135763", "120503"]
    assert rows[0]["fund_house"] == "Axis Mutual Fund"
    assert "Large Cap" in rows[0]["scheme_type"]
    assert rows[2]["fund_house"] == "SBI Mutual Fund", "the house switches mid-file"
    assert rows[1]["isin_reinvest"] == "INF846K01WQ6" and rows[0]["isin_reinvest"] is None


def test_a_scheme_with_no_declared_nav_is_absent_not_null():
    assert all(r["scheme_name"] != "Dormant Scheme" for r in amfi.parse(NAV_FILE))


def test_rows_keep_their_own_date_not_the_fetch_date():
    """AMFI's file mixes today's NAVs with stale ones; a 2018 NAV is not today's."""
    rows = amfi.parse(NAV_FILE)
    assert {str(r["nav_date"]) for r in rows} == {"2026-09-18", "2018-07-02"}


def test_nav_ingest_is_idempotent(tmp_path):
    cfg = Config(data_root=tmp_path).ensure()
    db = Database(cfg)
    db.migrate()

    class Fake:
        source, dataset, tier = "AMFI", "mf_nav", 1

        def fetch(self, d):
            from datetime import datetime, timezone

            from financial_brain.providers.base import FetchResult
            return FetchResult(payload=NAV_FILE, url="https://example/NAVAll.txt",
                               retrieved_at=datetime.now(timezone.utc),
                               filename="NAVAll.txt", content_type="text/plain")

    from financial_brain.ingest import mfnav
    with db.connect() as con:
        first = mfnav.ingest(con, cfg, date(2026, 9, 20), provider=Fake())
        second = mfnav.ingest(con, cfg, date(2026, 9, 20), provider=Fake())
        assert first["schemes"] == second["schemes"] == 3
        assert con.execute("SELECT count(*) FROM mf_nav").fetchone()[0] == 3
        assert con.execute("""SELECT count(*) FROM evidence
                              WHERE kind = 'mf_nav_file'""").fetchone()[0] == 1


ROBOTS = """
User-agent: GPTBot
Disallow: /

User-agent: *
Disallow: /stocks/company_info/*
Disallow: /financials/results/*
Disallow: /news/printpage/
Allow: /news/
Crawl-delay: 2
"""


def test_robots_understands_wildcards_where_the_stdlib_does_not():
    """urllib.robotparser matches rule paths by plain prefix, so "/stocks/company_info/*"
    matches nothing and a disallowed page reads as allowed. Under-blocking is the one
    mistake that matters here."""
    r = Robots(ROBOTS, "financial-brain/1.0")
    assert not r.can_fetch("https://x.com/stocks/company_info/print_main.php?sc_did=Y")
    assert not r.can_fetch("https://x.com/financials/results/abc")
    assert not r.can_fetch("https://x.com/news/printpage/story.html")
    assert r.can_fetch("https://x.com/news/business/markets/story-123.html")
    assert r.crawl_delay == 2

    import urllib.robotparser
    stdlib = urllib.robotparser.RobotFileParser()
    stdlib.parse(ROBOTS.splitlines())
    assert stdlib.can_fetch("financial-brain/1.0",
                            "https://x.com/stocks/company_info/print_main.php"), \
        "pinning the stdlib gap this class exists to close"


def test_longest_matching_rule_wins():
    r = Robots("User-agent: *\nDisallow: /news/\nAllow: /news/business/\n", "bot")
    assert r.can_fetch("https://x.com/news/business/story.html")
    assert not r.can_fetch("https://x.com/news/sport/story.html")


def test_a_host_off_the_allowlist_is_refused_without_a_request():
    p = NewsArticle(allowed={"moneycontrol.com"})
    ok, why = p.may_fetch("https://economictimes.indiatimes.com/article")
    assert not ok and "allowlist" in why


def test_only_article_urls_are_worth_fetching():
    assert is_article_url("https://www.moneycontrol.com/news/business/markets/x-123.html")
    assert not is_article_url("https://www.moneycontrol.com/")
    assert not is_article_url("https://www.moneycontrol.com")


def test_article_parse_takes_title_time_and_summary_only():
    page = (b'<html><head><meta property="og:title" content="Acme shares fall 8% '
            b'&amp; probe begins- Moneycontrol.com">'
            b'<meta property="og:description" content="The regulator said ...">'
            b'<meta property="article:published_time" content="2026-09-18T13:14:00+05:30">'
            b'</head><body>Full article body that we deliberately do not store.</body></html>')
    art = parse_article(page)
    assert art["title"] == "Acme shares fall 8% & probe begins"
    assert art["excerpt"] == "The regulator said ..."
    assert art["published_at"].year == 2026 and art["published_at"].hour == 13
    assert "body" not in str(art)


@pytest.mark.parametrize("url", ["https://www.moneycontrol.com/news/x-1.html"])
def test_fetch_is_refused_when_robots_cannot_be_read(url, monkeypatch):
    p = NewsArticle(allowed={"moneycontrol.com"})
    monkeypatch.setattr(p, "_rules", lambda u: None)
    ok, why = p.may_fetch(url)
    assert not ok and "robots" in why
