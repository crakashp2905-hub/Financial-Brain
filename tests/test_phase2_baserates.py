"""Base rates: how unusual is this, here and against the market? (research depth)

"The auditor resigned" reads the same whether it is the first in a decade or the third
in three years, and whether 5% or 0.1% of companies did the same last year. These tests
pin both comparisons, and that neither may look into the future.
"""
from __future__ import annotations

from datetime import date

import pytest

from financial_brain.config import Config
from financial_brain.features import baserates
from financial_brain.storage.db import Database


@pytest.fixture
def con(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as c:
        yield c


def _filing(con, isin, event, on, nid=None):
    con.execute("""INSERT INTO announcements (news_id, source, business_date, event_type,
                   materiality, evidence_key, observed_at, isin, company, headline)
                   VALUES (?,?,?,?,?,?,NOW(),?,?,?)""",
                [nid or f"{isin}-{event}-{on}", "BSE", on, event, "high",
                 f"k-{isin}-{on}", isin, f"Co {isin}", "x"])


def test_a_repeated_event_is_counted_here_and_priced_against_the_market(con):
    me = "INE000A01001"
    for on in (date(2024, 5, 1), date(2025, 6, 1), date(2026, 2, 1)):
        _filing(con, me, "AUDITOR_CHANGE", on)
    for i in range(18):                     # a market where auditor changes are rare
        _filing(con, f"INE00{i:02d}B01001", "RESULTS", date(2026, 3, 1))
    r = baserates.rate(con, me, "AUDITOR_CHANGE", date(2026, 9, 1))
    assert r.company_12m == 1 and r.company_36m == 3
    assert r.last_seen == date(2026, 2, 1)
    assert r.universe == 19 and r.market_companies == 1
    assert r.market_share_12m == pytest.approx(1 / 19)
    assert r.unusual()
    assert "3 times here in three years" in r.describe()
    assert "5.3% of 19 companies" in r.describe()


def test_a_common_event_is_not_flagged_as_unusual(con):
    me = "INE000A01001"
    for on in (date(2025, 6, 1), date(2026, 6, 1)):
        _filing(con, me, "RESULTS", on)
    for i in range(10):
        _filing(con, f"INE00{i:02d}B01001", "RESULTS", date(2026, 3, 1))
    r = baserates.rate(con, me, "RESULTS", date(2026, 9, 1))
    assert r.company_36m == 2
    assert r.market_share_12m == 1.0, "everyone files results"
    assert not r.unusual(), "repetition only matters when the market does not do it"


def test_a_first_occurrence_says_so(con):
    me = "INE000A01001"
    _filing(con, me, "INSOLVENCY", date(2026, 8, 1))
    r = baserates.rate(con, me, "INSOLVENCY", date(2026, 9, 1))
    assert r.company_36m == 1 and "first in three years here" in r.describe()


def test_nothing_after_the_as_of_date_is_counted(con):
    """A base rate computed for a past date must not see what happened later."""
    me = "INE000A01001"
    _filing(con, me, "AUDITOR_CHANGE", date(2026, 1, 1))
    _filing(con, me, "AUDITOR_CHANGE", date(2026, 8, 1))
    early = baserates.rate(con, me, "AUDITOR_CHANGE", date(2026, 3, 1))
    late = baserates.rate(con, me, "AUDITOR_CHANGE", date(2026, 9, 1))
    assert early.company_36m == 1 and late.company_36m == 2
    assert early.last_seen == date(2026, 1, 1)


def test_repeated_returns_only_the_patterns_worth_showing(con):
    me = "INE000A01001"
    for on in (date(2025, 1, 1), date(2026, 1, 1)):
        _filing(con, me, "PROMOTER_PLEDGE", on)
    _filing(con, me, "INSOLVENCY", date(2026, 2, 1))
    got = baserates.repeated(con, me, date(2026, 9, 1),
                             ("PROMOTER_PLEDGE", "INSOLVENCY", "AUDITOR_CHANGE"))
    assert [r.event_type for r in got] == ["PROMOTER_PLEDGE"], "one-offs are not a pattern"


def test_a_heavy_but_normal_filer_is_not_called_a_pattern(con):
    """NRB Bearing files 47 shareholding disclosures in three years; so do its peers.
    Rarity of the event type alone would flag it, which is why the company is measured
    against what other filers of the same type manage."""
    me = "INE000A01001"
    for i in range(20):
        _filing(con, me, "PROMOTER_PLEDGE", date(2026, 1, 1), nid=f"me-{i}")
    for p in range(3):                       # peers file just as many
        for i in range(25):
            _filing(con, f"INE00{p}B01001", "PROMOTER_PLEDGE", date(2026, 1, 1),
                    nid=f"p{p}-{i}")
    for i in range(60):                      # a wide market that mostly does not
        _filing(con, f"INE9{i:02d}C01001", "RESULTS", date(2026, 3, 1))
    r = baserates.rate(con, me, "PROMOTER_PLEDGE", date(2026, 9, 1))
    assert r.company_36m == 20
    assert r.market_share_12m <= 0.10, "the event type is rare across the market"
    assert not r.unusual(), "but not for a company that files these at all"
