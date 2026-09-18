"""Phase 1 - BSE corporate-action feed: purpose parsing and point-in-time ISIN mapping."""
from __future__ import annotations

import json
from datetime import date

import pytest

from financial_brain.providers.bse_corpact import BSECorporateActionsProvider, parse_purpose


@pytest.mark.parametrize("purpose, expected", [
    # real strings surveyed from the feed, 2015-2026
    ("Stock  Split From Rs.10/- to Rs.2/-", ("SPLIT", 1.0, 5.0, None)),
    ("Stock Split From Rs.10/- to Rs.5/-", ("SPLIT", 1.0, 2.0, None)),
    ("Stock Split From Rs.10/- to Rs.1/-", ("SPLIT", 1.0, 10.0, None)),
    ("Stock Split From Rs.5/- to Rs.2/-", ("SPLIT", 2.0, 5.0, None)),
    ("Bonus issue 1:1", ("BONUS", 1.0, 2.0, None)),          # 1 new per 1 held -> x0.5
    ("Bonus issue 1:3", ("BONUS", 3.0, 4.0, None)),          # Wipro 2019 -> x0.75
    ("Bonus issue 5:1", ("BONUS", 1.0, 6.0, None)),          # Shakti Pumps 2024 -> x1/6
    ("Bonus issue 2:5", ("BONUS", 5.0, 7.0, None)),
    ("Final Dividend - Rs. - 2.5000", ("DIVIDEND", None, None, 2.5)),
    ("Interim Dividend - Rs. - 140.0000", ("DIVIDEND", None, None, 140.0)),
    ("Special Dividend - Rs. - 4.0000", ("DIVIDEND", None, None, 4.0)),
    ("Right Issue of Equity Shares", ("RIGHTS", None, None, None)),
    ("Buy Back of Shares", ("BUYBACK", None, None, None)),
    ("Spin Off", ("SPIN_OFF", None, None, None)),
    ("Reduction of Capital", ("CAPITAL_REDUCTION", None, None, None)),
    ("Amalgamation", ("MERGER", None, None, None)),
    ("Resolution Plan -Suspension", ("SUSPENSION", None, None, None)),
    ("E.G.M.", ("MEETING", None, None, None)),
])
def test_purpose_parsing(purpose, expected):
    assert parse_purpose(purpose) == expected


def test_a_face_value_increase_is_not_read_as_a_split_factor():
    """Strict: FV going *up* is a consolidation; we do not guess its factor."""
    assert parse_purpose("Stock Split From Rs.1/- to Rs.10/-") == ("SPLIT", None, None, None)


def test_unknown_purpose_is_kept_without_a_factor():
    assert parse_purpose("Something New Entirely") == ("OTHER", None, None, None)


def test_feed_rows_parse_with_dates():
    payload = json.dumps([{
        "scrip_code": 505790, "short_name": "SCHAEFFLER", "Ex_date": "08 Feb 2022",
        "Purpose": "Stock  Split From Rs.10/- to Rs.2/-", "RD_Date": "09 Feb 2022"}]).encode()
    [a] = BSECorporateActionsProvider.parse(payload)
    assert (a.scrip_code, a.ex_date, a.record_date) == ("505790", date(2022, 2, 8),
                                                        date(2022, 2, 9))
    assert (a.action_type, a.ratio_from, a.ratio_to) == ("SPLIT", 1.0, 5.0)


# ------------------------------------------------------------- ingest + reconcile
from datetime import datetime, timezone

from financial_brain.config import Config
from financial_brain.providers.base import FetchResult
from financial_brain.storage.db import Database


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(data_root=tmp_path).ensure()


@pytest.fixture
def db(cfg) -> Database:
    d = Database(cfg)
    d.migrate()
    return d


class FakeFeed(BSECorporateActionsProvider):
    def __init__(self, rows):
        self.rows, self.calls = rows, 0

    def fetch_month(self, year, month):
        self.calls += 1
        body = json.dumps([r for r in self.rows
                           if r["Ex_date"].endswith(f"{month:02d} {year}") or True]).encode()
        return FetchResult(payload=body, url=f"https://api.bse.test/{year}{month}",
                           retrieved_at=datetime.now(timezone.utc),
                           filename=f"bse_corpact_{year}{month:02d}.json",
                           content_type="application/json")


OLD, NEW = "INE513A01014", "INE513A01022"            # Schaeffler 1:5, 2022-02-08


def _listings(con):
    con.execute("INSERT INTO security_listings VALUES (?, 'BSE', 'SCHAEFFLER', 'A', "
                "'505790', DATE '2015-01-01', DATE '2022-02-07')", [OLD])
    con.execute("INSERT INTO security_listings VALUES (?, 'BSE', 'SCHAEFFLER', 'A', "
                "'505790', DATE '2022-02-08', DATE '2026-09-18')", [NEW])


ROWS = [{"scrip_code": 505790, "short_name": "SCHAEFFLER", "Ex_date": "08 Feb 2022",
         "Purpose": "Stock  Split From Rs.10/- to Rs.2/-", "RD_Date": "09 Feb 2022"},
        {"scrip_code": 505790, "short_name": "SCHAEFFLER", "Ex_date": "20 Apr 2021",
         "Purpose": "Final Dividend - Rs. - 18.0000", "RD_Date": "21 Apr 2021"},
        {"scrip_code": 999999, "short_name": "UNKNOWN", "Ex_date": "10 Feb 2022",
         "Purpose": "Bonus issue 1:1", "RD_Date": ""}]


def test_split_maps_to_the_isin_in_force_on_the_ex_date(cfg, db):
    from financial_brain.ingest import corpact_feed
    with db.connect() as con:
        _listings(con)
        st = corpact_feed.ingest(con, cfg, date(2022, 2, 1), date(2022, 2, 1),
                                 provider=FakeFeed(ROWS), today=date(2026, 9, 18))
        got = con.execute("SELECT isin, action_type, ratio_from, ratio_to, amount "
                          "FROM corporate_actions ORDER BY ex_date").fetchall()
    assert got == [(OLD, "DIVIDEND", None, None, 18.0),     # before the split: old ISIN
                   (NEW, "SPLIT", 1.0, 5.0, None)]          # on the ex-date: new ISIN
    assert st["unmapped"] == 1, "a scrip we never listed is counted, not guessed"


def test_past_months_come_from_the_lake_and_ingest_is_idempotent(cfg, db):
    from financial_brain.ingest import corpact_feed
    feed = FakeFeed(ROWS)
    with db.connect() as con:
        _listings(con)
        corpact_feed.ingest(con, cfg, date(2022, 2, 1), date(2022, 2, 1), provider=feed,
                            today=date(2026, 9, 18))
        second = corpact_feed.ingest(con, cfg, date(2022, 2, 1), date(2022, 2, 1),
                                     provider=feed, today=date(2026, 9, 18))
        n = con.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0]
    assert feed.calls == 1, "a past month already in the lake is never re-fetched"
    assert second["recorded"] == 0 and n == 2


def test_reported_action_supersedes_a_derived_one_in_the_factors(cfg, db):
    from financial_brain.corpactions.detect import rebuild_derived_factors
    from financial_brain.ingest import corpact_feed
    with db.connect() as con:
        _listings(con)
        con.execute("""INSERT INTO corporate_actions (action_id, isin, action_type, ex_date,
            source, source_tier, observed_at, confidence, derived_factor)
            VALUES ('derived', ?, 'SPLIT', DATE '2022-02-08', 'NSE', 1, NOW(),
                    'corroborated_succession', 0.2)""", [NEW])
        corpact_feed.ingest(con, cfg, date(2022, 2, 1), date(2022, 2, 1),
                            provider=FakeFeed(ROWS[:1]), today=date(2026, 9, 18))
        rebuild_derived_factors(con)
        f = con.execute("SELECT price_factor, derived_from FROM adjustment_factors "
                        "WHERE isin = ?", [NEW]).fetchall()
        rec = corpact_feed.reconcile(con)
    assert len(f) == 1 and abs(f[0][0] - 0.2) < 1e-9, "one factor, not 0.2 x 0.2"
    assert f[0][1] != "derived", "the reported action is the one applied"
    assert rec["derived_matched"] == 1 and rec["precision"] == 1.0
