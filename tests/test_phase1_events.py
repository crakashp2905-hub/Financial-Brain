"""Phase 1 - deterministic announcement classification."""
from __future__ import annotations

import pytest

from financial_brain.events.classify import classify


@pytest.mark.parametrize("cat, sub, headline, expected", [
    # (category, subcategory) pairs observed in BSE's feed, 2016-2026
    ("Result", "Financial Results", "", ("RESULTS", "high")),
    ("Integrated Filing", "Integrated Filing (Financial)", "", ("RESULTS", "high")),
    ("Company Update", "Resignation of Statutory Auditors", "", ("AUDITOR_RESIGNATION", "high")),
    ("Insider Trading / SAST",
     "Disclosures under Reg. 31(1) and 31(2) of SEBI (SAST) Regulations, 2011", "",
     ("PROMOTER_PLEDGE", "high")),
    ("Insider Trading / SAST",
     "Disclosures under Reg. 29(2) of SEBI (SAST) Regulations, 2011", "",
     ("SUBSTANTIAL_ACQUISITION", "medium")),
    ("Insider Trading / SAST", "Closure of Trading Window", "", ("TRADING_WINDOW", "low")),
    ("Company Update", "Credit Rating", "", ("CREDIT_RATING", "high")),
    ("Company Update", "Clarification", "", ("CLARIFICATION", "high")),
    ("Company Update", "Acquisition", "", ("ACQUISITION", "high")),
    ("Company Update", "Scheme of Arrangement", "", ("SCHEME", "high")),
    ("Company Update", "Raising of Funds", "", ("FUND_RAISING", "high")),
    ("Company Update", "Resignation of Chief Financial Officer (CFO)", "",
     ("MANAGEMENT_CHANGE", "high")),
    ("Company Update", "Change in Directorate", "", ("BOARD_CHANGE", "medium")),
    ("Board Meeting", "Outcome of Board Meeting", "", ("BOARD_OUTCOME", "medium")),
    ("Board Meeting", "Board Meeting", "", ("BOARD_MEETING", "medium")),
    ("Corp. Action", "Dividend", "", ("DIVIDEND", "medium")),
    ("Corp Action", "Daily Buy Back of equity shares", "", ("BUYBACK", "medium")),
    ("Company Update", "Earnings Call Transcript", "", ("EARNINGS_CALL", "medium")),
    ("Company Update", "Analyst / Investor Meet", "", ("INVESTOR_MEET", "medium")),
    ("AGM/EGM", "Postal Ballot", "", ("SHAREHOLDER_MEETING", "medium")),
    ("Others", "Reg. 34 (1) Annual Report", "", ("COMPLIANCE", "low")),
    ("Company Update", "Newspaper Publication", "", ("COMPLIANCE", "low")),
])
def test_exchange_labels_drive_the_type(cat, sub, headline, expected):
    assert classify(cat, sub, headline)[:2] == expected


@pytest.mark.parametrize("headline", [
    "Bharat Electronics Ltd bags orders worth Rs 572 crore",
    "Company secures Letter of Award for EPC contract",
    "KEC International wins new orders of Rs 1,025 crore",
    "Receipt of work order from NHAI",
])
def test_order_wins_are_found_in_coarse_labels(headline):
    assert classify("Company Update", "General", headline)[:2] == ("ORDER_WIN", "high")


def test_order_keywords_do_not_override_a_specific_label():
    """'order' in a results headline is still a result."""
    assert classify("Result", "Financial Results",
                    "Results; order book at record high")[0] == "RESULTS"


def test_a_plain_general_update_stays_low():
    assert classify("Company Update", "General", "Change in registered office")[:2] == \
        ("GENERAL", "low")


def test_every_classification_says_why():
    assert classify("Company Update", "Credit Rating")[2].startswith("subcategory")


# ------------------------------------------------------------------ ingestion
import json
from datetime import date, datetime, timezone

from financial_brain.config import Config
from financial_brain.providers.base import FetchResult, NotPublished
from financial_brain.providers.bse_announcements import BSEAnnouncementsProvider
from financial_brain.storage.db import Database


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(data_root=tmp_path).ensure()


@pytest.fixture
def db(cfg) -> Database:
    d = Database(cfg)
    d.migrate()
    return d


def _row(nid, scrip, sub, headline="", cat="Company Update", upper=True):
    r = {"NEWSID": nid, "SCRIP_CD": scrip, "SLONGNAME": "Test Ltd", "CATEGORYNAME": cat,
         "SUBCATNAME": sub, "HEADLINE": headline, "NEWSSUB": f"Test Ltd - {scrip} - {sub}",
         "CRITICALNEWS": 0, "NEWS_DT": "2026-09-17T18:00:00.1",
         "DissemDT": "2026-09-17T18:00:05.3", "News_submission_dt": "2026-09-17T17:59:00",
         "ATTACHMENTNAME": "a.pdf"}
    return r if upper else {("BSENewsid" if k == "NEWSID" else k): v for k, v in r.items()}


class FakeAnn(BSEAnnouncementsProvider):
    def __init__(self, days):
        self.days, self.calls = days, 0

    def fetch(self, d):
        self.calls += 1
        if d not in self.days:
            raise NotPublished("none")
        rows, declared = self.days[d]
        pages = [json.dumps({"Table": rows[i:i + 50], "Table1": [{"ROWCNT": declared}]})
                 for i in range(0, max(len(rows), 1), 50)]
        body = json.dumps({"date": d.isoformat(), "rowcount": declared, "pages": pages})
        return FetchResult(payload=body.encode(), url="https://api.bse.test/ann",
                           retrieved_at=datetime.now(timezone.utc),
                           filename=f"ann_{d:%Y%m%d}.json", content_type="application/json")


D = date(2026, 9, 17)


def _job(cfg, days):
    from financial_brain.ingest.announcements import AnnouncementsJob
    return AnnouncementsJob(cfg, provider=FakeAnn(days))


def test_announcements_are_classified_and_mapped_to_isin(cfg, db):
    with db.connect() as con:
        con.execute("INSERT INTO security_listings VALUES ('INE263A01024', 'BSE', 'BEL', "
                    "'A', '500049', DATE '2015-01-01', DATE '2026-09-18')")
    rows = [_row("n1", 500049, "General", "BEL bags orders worth Rs 572 crore"),
            _row("n2", 500049, "Credit Rating"),
            _row("n3", 999999, "Financial Results", cat="Result")]
    [res] = _job(cfg, {D: (rows, 3)}).run_range(D, D)
    with db.connect() as con:
        got = con.execute("SELECT news_id, isin, event_type, materiality FROM announcements "
                          "ORDER BY news_id").fetchall()
        ts = con.execute("SELECT published_at FROM announcements WHERE news_id='n1'").fetchone()[0]
    assert res.status == "ok" and res.rows_out == 3
    assert got == [("n1", "INE263A01024", "ORDER_WIN", "high"),
                   ("n2", "INE263A01024", "CREDIT_RATING", "high"),
                   ("n3", None, "RESULTS", "high")], "unlisted scrip: kept, ISIN unknown"
    assert ts == datetime(2026, 9, 17, 18, 0, 5), "publication = dissemination time"


def test_a_day_short_of_bses_declared_count_is_partial(cfg, db):
    rows = [_row(f"n{i}", 500049, "General") for i in range(10)]
    [res] = _job(cfg, {D: (rows, 40)}).run_range(D, D)     # BSE said 40, we got 10
    assert res.status == "ok_partial" and "INCOMPLETE" in res.message


def test_replay_uses_the_lake_and_is_idempotent(cfg, db):
    days = {D: ([_row("n1", 500049, "General")], 1)}
    job = _job(cfg, days)
    job.run_range(D, D)
    job.provider.days = {}                                 # network now empty
    [again] = job.run_range(D, D, force=True)
    assert job.provider.calls == 1, "lake-first: the day is never fetched twice"
    assert again.status == "ok"
    with db.connect() as con:
        n = con.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
    assert n == 1, "a replay re-classifies in place; it never duplicates"


def test_field_names_are_read_case_insensitively():
    """BSE's field casing varies by year (BSENEWSID vs BSENewsid); every field is read
    case-insensitively so a casing change cannot silently blank a column."""
    mixed = {"NewsId": "m1", "Scrip_Cd": 500049, "SLongName": "Test Ltd",
             "CategoryName": "Company Update", "SubCatName": "Credit Rating",
             "Headline": "h", "NewsSub": "s", "DISSEMDT": "2016-03-01T10:00:00"}
    body = json.dumps({"date": "2016-03-01", "rowcount": 1, "pages": [json.dumps(
        {"Table": [mixed], "Table1": [{"ROWCNT": 1}]})]}).encode()
    declared, [r] = BSEAnnouncementsProvider.parse(body)
    assert declared == 1
    assert (r["news_id"], r["scrip_code"], r["subcategory"]) == ("m1", "500049", "Credit Rating")
    assert r["published_at"] == datetime(2016, 3, 1, 10, 0)


@pytest.mark.parametrize("cat, sub, headline, expected", [
    ("Company Update", "Award of Order / Receipt of Order", "Receipt of order worth Rs. 263.25 Crore",
     "ORDER_WIN"),
    # APL Apollo 2026-08-22: a tax demand is not a business win
    ("Company Update", "General", "The Company has received order of revised demand from Deputy "
     "Commissioner (Appeals), Hosur under Section 73 of the TNGST Act. 2017 for FY 2019-20.",
     "LEGAL_REGULATORY"),
    ("Company Update", "Award of Order / Receipt of Order",
     "Receipt of GST demand order and penalty", "LEGAL_REGULATORY"),
    ("Company Update", "Intimation of meeting of Committee of Creditors", "", "INSOLVENCY"),
    ("Company Update", "Restructuring", "", "SCHEME"),
    ("Company Update", "Issue of Securities", "", "FUND_RAISING"),
    ("New Listing", "New Listing", "", "NEW_LISTING"),
    ("Others", "Outcome without intimation", "", "BOARD_OUTCOME"),
    ("Company Update", "Certificate under Reg. 74 (5) of SEBI (DP) Regulations, 2018", "",
     "COMPLIANCE"),
])
def test_real_residue_from_the_first_90_days(cat, sub, headline, expected):
    assert classify(cat, sub, headline)[0] == expected


@pytest.mark.parametrize("text, legal", [
    ("syntax error", False), ("definitely fine", False),
    ("appeal to shareholders to vote", False), ("notice of AGM", False),
    ("Receipt of GST demand order and penalty", True),
    ("SEBI order imposing penalty", True), ("NCLT admits petition", True),
    ("an order worth Rs 263 crore", False),
])
def test_legal_keywords_need_word_boundaries_and_real_legal_phrasing(text, legal):
    from financial_brain.events.classify import _LEGAL
    assert bool(_LEGAL.search(text)) is legal
