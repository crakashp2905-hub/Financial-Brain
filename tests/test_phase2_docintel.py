"""Reading the document a filing points at (C11).

Every rule here was forced by a real filing. The first run against BSE attachments
minted an investor deck's "addressable market demand of Rs 65,000 crore" as a tax
demand, a credit-rating release's programme size as a fund raise, and a demand the
tribunal had *set aside* as a live liability.
"""
from __future__ import annotations

from datetime import date

import pytest

from financial_brain.config import Config
from financial_brain.docintel import extract
from financial_brain.providers.bse_filing import BSEFiling
from financial_brain.storage.db import Database

CRORE = 10 ** 7


def test_a_number_must_be_named_to_be_a_fact():
    """An unnamed figure is not a claim, whatever its size."""
    assert extract.facts("total consideration of Rs 500 crore", event_type=None) == []
    assert extract.classify("turnover was Rs 500 crore",
                            _amount("turnover was Rs 500 crore")) == "amount"


def _amount(text):
    from financial_brain.docintel import amounts
    return amounts.largest(text)


@pytest.mark.parametrize("text,event,kind", [
    ("has received a Work Order worth Rs. 161 crores", "ORDER_WIN", "order_value"),
    ("receipt of Demand Order under Section 156 for Rs 45 crore", "LEGAL_REGULATORY",
     "tax_demand"),
    ("imposed a penalty of Rs 1.74 crore on the Company", "LEGAL_REGULATORY", "penalty"),
])
def test_a_named_number_in_the_right_filing_is_a_fact(text, event, kind):
    got = extract.facts(text, event_type=event)
    assert [f.kind for f in got] == [kind]
    assert got[0].context, "a claim must carry the sentence it came from"


def test_a_number_that_contradicts_the_filing_type_is_dropped():
    """The document says how much; the exchange says what happened. A tax demand
    extracted from an order-win filing is noise."""
    text = "receipt of Demand Order under Section 156 for Rs 45 crore"
    assert extract.facts(text, event_type="ORDER_WIN") == []


def test_an_unknown_filing_type_mints_nothing():
    """Default deny. A CRISIL release filed as GENERAL put a rated debt programme
    through as a 'fund raise' when unknown types were permissive."""
    text = "CRISIL reaffirmed its ratings on bank facilities of Rs. 1087.5 Crore"
    assert extract.facts(text, event_type="GENERAL") == []
    assert extract.facts(text, event_type=None) == []


def test_a_market_size_is_not_a_tax_demand():
    text = "Indian addressable market demand of Rs 65,000 crore by 2030"
    assert extract.facts(text, event_type="LEGAL_REGULATORY") == []


def test_a_demand_that_was_set_aside_is_relief_not_a_liability():
    text = ("The Hon'ble ITAT has allowed the appeal filed by the Company and completely "
            "set aside the demand order of Rs. 1.17 crore")
    got = extract.facts(text, event_type="LEGAL_REGULATORY")
    assert [f.kind for f in got] == ["demand_set_aside"]
    assert got[0].value == pytest.approx(1.17 * CRORE)


def test_dividend_per_share_is_matched_below_the_money_floor():
    """Rs 0.12 per share is far under the floor for company-scale amounts, and is the
    whole point of a dividend filing whose headline said 'Please refer the Enclosed
    files.'"""
    text = "payment of Interim Dividend of Rs 0.12/- per equity share of face value Rs 10"
    got = extract.facts(text, event_type="DIVIDEND")
    assert got[0].kind == "dividend_per_share" and got[0].value == pytest.approx(0.12)


def test_a_scan_says_so_rather_than_reporting_no_facts():
    assert extract.looks_scanned("   \n  ", pages=3)
    assert not extract.looks_scanned("x" * 500, pages=3)


def test_the_historical_attachment_path_is_tried_too():
    """BSE moves older attachments from AttachLive to AttachHis; the announcement keeps
    the path that was right on the day."""
    p = BSEFiling()
    live = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/abc.pdf"
    assert p.candidates(live) == [live, live.replace("AttachLive", "AttachHis")]


def test_a_non_bse_url_is_refused():
    from financial_brain.providers.base import FetchError
    with pytest.raises(FetchError):
        BSEFiling().fetch_url("https://example.com/file.pdf")


class FakeFiling:
    """Serves one tiny PDF for any URL."""
    PDF = None

    def fetch_url(self, url):
        from datetime import datetime, timezone

        from financial_brain.providers.base import FetchResult
        return FetchResult(payload=self.PDF, url=url, retrieved_at=datetime.now(timezone.utc),
                           filename="a.pdf", content_type="application/pdf")


def _one_page_pdf(body: str) -> bytes:
    import io

    import pypdf
    w = pypdf.PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_reading_a_day_is_idempotent_and_records_what_it_could_not_read(tmp_path):
    cfg = Config(data_root=tmp_path).ensure()
    db = Database(cfg)
    db.migrate()
    from financial_brain.ingest import filings
    FakeFiling.PDF = _one_page_pdf("")          # a blank page: no text, like a scan
    with db.connect() as con:
        con.execute("""INSERT INTO announcements (news_id, source, business_date,
            event_type, materiality, evidence_key, observed_at, company, attachment)
            VALUES ('n1','BSE',DATE '2026-09-18','ORDER_WIN','high','k',NOW(),'Acme',
                    'https://www.bseindia.com/xml-data/corpfiling/AttachLive/a.pdf')""")
        first = filings.read_day(con, cfg, date(2026, 9, 18), provider=FakeFiling())
        again = filings.read_day(con, cfg, date(2026, 9, 18), provider=FakeFiling())
        assert first["read"] == 1 and first["scanned_no_text"] == 1
        assert again["candidates"] == 0, "a document already read is not fetched again"
        status = con.execute("SELECT status FROM filing_documents").fetchone()[0]
        assert status == "scanned (no text layer)"
