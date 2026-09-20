"""Public sources added in Phase 2: AMFI NAVs, and fetching a filing's linked article.

The fetch tests are offline on purpose - what needs pinning is the *politeness* logic
(allowlist, robots with wildcards, what counts as an article), not the network.
"""
from __future__ import annotations

from contextlib import contextmanager
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


SCREENER_PAGE = ("""<html><body><h1>Acme Industries Ltd</h1>
<ul id="top-ratios">
  <li class="flex flex-space-between"><span class="name">Market Cap</span>
      <span class="value">₹ 16,59,630 Cr.</span></li>
  <li class="flex flex-space-between"><span class="name">Current Price</span>
      <span class="value">₹ 1,226</span></li>
  <li class="flex flex-space-between"><span class="name">High / Low</span>
      <span class="value">₹ 1,612 / 1,226</span></li>
  <li class="flex flex-space-between"><span class="name">Stock P/E</span>
      <span class="value">42.3</span></li>
  <li class="flex flex-space-between"><span class="name">ROCE</span>
      <span class="value">7.78 %</span></li>
</ul></body></html>""").encode()


def test_screener_ratios_parse_with_units():
    from financial_brain.providers.screener import parse_company
    r = parse_company(SCREENER_PAGE)
    assert r["name"] == "Acme Industries Ltd"
    # Indian digit grouping: 16,59,630 crore, so commas are not thousands separators.
    assert r["ratios"]["Market Cap"] == {"raw": "₹ 16,59,630 Cr.",
                                         "value": 16_59_630 * 1e7, "unit": "INR"}
    assert r["ratios"]["Stock P/E"]["value"] == 42.3 and r["ratios"]["Stock P/E"]["unit"] is None
    assert r["ratios"]["ROCE"] == {"raw": "7.78 %", "value": 7.78, "unit": "PCT"}


def test_a_paired_ratio_becomes_two_facts():
    from financial_brain.providers.screener import parse_company
    ratios = parse_company(SCREENER_PAGE)["ratios"]
    assert ratios["High"]["value"] == 1612.0 and ratios["Low"]["value"] == 1226.0
    assert "High / Low" not in ratios


def test_screener_refuses_paths_its_robots_disallows(monkeypatch):
    from financial_brain.providers.robots import Robots
    from financial_brain.providers.screener import Screener
    s = Screener()
    monkeypatch.setattr(s, "_rules", lambda: Robots(
        "User-agent: *\nDisallow: /user/*\nDisallow: /*?q=\n"
        "Disallow: /company/source/quarter/*\n", "financial-brain/1.0"))
    assert s.may_fetch("https://www.screener.in/company/RELIANCE/")[0]
    assert not s.may_fetch("https://www.screener.in/company/source/quarter/1/")[0]
    assert not s.may_fetch("https://www.screener.in/user/me")[0]
    assert not s.may_fetch("https://www.moneycontrol.com/company/X/")[0]


@contextmanager
def _fundamentals_con(tmp_path, close: float):
    cfg = Config(data_root=tmp_path).ensure()
    db = Database(cfg)
    db.migrate()
    with db.connect() as con:
        # migrate() defines eod_prices as a view over the curated parquet; for this test
        # a view returning one row is enough and keeps the object's type unchanged.
        con.execute(f"""CREATE OR REPLACE VIEW eod_prices AS SELECT
                        DATE '2026-09-18' AS business_date, 'ACME' AS ticker,
                        CAST({close} AS DOUBLE) AS close_price""")
        yield cfg, con


class FakeScreener:
    source, dataset, tier = "SCREENER", "company", 3

    def fetch_company(self, symbol, *, consolidated=False):
        from datetime import datetime, timezone

        from financial_brain.providers.base import FetchResult
        return FetchResult(payload=SCREENER_PAGE, url=f"https://www.screener.in/company/{symbol}/",
                           retrieved_at=datetime.now(timezone.utc), filename=f"{symbol}.html",
                           content_type="text/html")


def test_a_tier3_ratio_is_cross_checked_against_our_own_close(tmp_path):
    """Screener compiles filings, it does not file them. Where we can compute the same
    number from Tier-1 data we compare rather than trust."""
    from financial_brain.ingest import fundamentals
    with _fundamentals_con(tmp_path, close=1230.0) as (cfg, con):   # within 2% of 1226
        r = fundamentals.fetch_company(con, cfg, "ACME", provider=FakeScreener(),
                                       today=date(2026, 9, 20))
        assert r["price_check"]["agrees"] is True
        q = con.execute("SELECT quality FROM evidence WHERE kind='fundamentals'").fetchone()
        assert q[0] == "ok"


def test_a_price_that_disagrees_is_recorded_as_disputed(tmp_path):
    from financial_brain.ingest import fundamentals
    with _fundamentals_con(tmp_path, close=900.0) as (cfg, con):    # 36% off 1226
        r = fundamentals.fetch_company(con, cfg, "ACME", provider=FakeScreener(),
                                       today=date(2026, 9, 20))
        assert r["price_check"]["agrees"] is False
        row = con.execute("""SELECT quality, confidence FROM evidence
                             WHERE kind = 'fundamentals'""").fetchone()
        assert row == ("disputed", "low"), "a disagreement is kept, not silently averaged"


def test_a_screener_snapshot_is_point_in_time(tmp_path):
    """A snapshot taken today must not appear in a dossier reconstructing last week."""
    import json

    from financial_brain.committee.dossier import valuation_as_of
    cfg = Config(data_root=tmp_path).ensure()
    db = Database(cfg)
    db.migrate()
    with db.connect() as con:
        con.execute("""CREATE OR REPLACE VIEW eod_prices AS
                       SELECT 'ACME' AS ticker, 'INE000A01001' AS isin""")
        con.execute("""INSERT INTO company_fundamentals (symbol, fetched_on, company_name,
                       ratios, price_check, lake_key, observed_at) VALUES
                       ('ACME', DATE '2026-09-20', 'Acme Ltd', ?, NULL, 'k', NOW())""",
                    [json.dumps({"Stock P/E": {"raw": "42.3", "value": 42.3, "unit": None}})])
        assert valuation_as_of(con, "INE000A01001", date(2026, 9, 18)) is None
        got = valuation_as_of(con, "INE000A01001", date(2026, 9, 20))
        assert got and "Stock P/E 42.3" in got["text"]


def test_a_disputed_price_is_stated_in_the_dossier_text(tmp_path):
    import json

    from financial_brain.committee.dossier import valuation_as_of
    cfg = Config(data_root=tmp_path).ensure()
    db = Database(cfg)
    db.migrate()
    with db.connect() as con:
        con.execute("""CREATE OR REPLACE VIEW eod_prices AS
                       SELECT 'ACME' AS ticker, 'INE000A01001' AS isin""")
        con.execute("""INSERT INTO company_fundamentals (symbol, fetched_on, company_name,
                       ratios, price_check, lake_key, observed_at) VALUES
                       ('ACME', DATE '2026-09-20', 'Acme Ltd', ?, ?, 'k', NOW())""",
                    [json.dumps({"ROCE": {"raw": "7.78 %", "value": 7.78, "unit": "PCT"}}),
                     json.dumps({"our_close": 900.0, "screener_price": 1226.0,
                                 "drift": 0.362, "agrees": False})])
        got = valuation_as_of(con, "INE000A01001", date(2026, 9, 20))
        assert "disputed" in got["text"], "a committee must see the disagreement"
