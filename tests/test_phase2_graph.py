"""P2-4 knowledge graph: filer extraction from BSE disclosure headlines."""
from __future__ import annotations

import pytest

from financial_brain.graph import extract as x

NEW = ("The Exchange has received Disclosure under Regulation 31(1) and 31(2) of SEBI "
       "(Substantial Acquisition of Shares & Takeovers) Regulations, 2011 on January 25, "
       "2024 for {}")


@pytest.mark.parametrize("headline, name", [
    (NEW.format("Siddeshwari Tradex Pvt Ltd"), "Siddeshwari Tradex Pvt Ltd"),
    ("The Exchange has received the disclosure under Regulation 29(2) of SEBI (Substantial "
     "Acquisition of Shares & Takeovers) Regulations, 2011 for Prakash Chhaganlal Kanugo & "
     "PACs", "Prakash Chhaganlal Kanugo"),
    ("The Exchange has received the disclosure under Regulation 29(2) of SEBI (SAST) "
     "Regulations, 2011 for Dhirubhai Jinabhai Bhanderi & Others",
     "Dhirubhai Jinabhai Bhanderi"),
    ("The Exchange has received Disclosure under Regulation 31(1) and 31(2) of SEBI (SAST) "
     "Regulations, 2011 for January 04, 2022 for Spank Management Services Pvt Ltd",
     "Spank Management Services Pvt Ltd"),
    ("The Exchange has received the disclosure under Regulation 29(1) of SEBI (SAST) "
     "Regulations, 2011 for Piramal Trusteeship Services Pvt Ltd Lender : Piramal Capital",
     "Piramal Trusteeship Services Pvt Ltd"),
    ("JSW Holdings Ltd has submitted the disclosure under Regulation 31(1) and 31(2) of SEBI "
     "(SAST) Regulations, 2011 to BSE", "JSW Holdings Ltd"),
    ("Suchitra Dhanani & Others has now submitted the disclosures under Reg. 7(1A) of SEBI "
     "(SAST) Regulations, 1997", "Suchitra Dhanani"),
])
def test_filer_is_extracted_from_both_templates(headline, name):
    assert x.filer(headline) == name


@pytest.mark.parametrize("headline", [
    "The Exchange has received the disclosure under Regulation 10(5) in respect of "
    "acquisition under Regulation 10(1)(a) of SEBI (Substantial Acquisition ....",
    "The Exchange has received the Disclosures of reasons for encumbrance by promoter of "
    "listed companies under Reg. 31(1) read with Regulation 28(3) of SEBI (SAST) "
    "Regulations, 2011 on February ....",
    "We wish to inform you that the Company has received the disclosures from all of the "
    "Promoters under Regulation 29(2)",
    "The Exchange has received the disclosure under Regulation 29(2) of SEBI (SAST) "
    "Regulations, 2011 for",
])
def test_no_filer_when_the_headline_names_none(headline):
    assert not x.filer(headline)


def test_relation_reads_the_regulation():
    assert x.relation("Disclosures under Reg. 31(1) and 31(2) of SEBI (SAST) Regulations, "
                      "2011") == "PLEDGE"
    assert x.relation("Disclosures under Reg. 10(6) of SEBI (SAST) Regulations, 2011") == \
        "EXEMPT"
    assert x.relation("Disclosures under Reg. 29(2) of SEBI (SAST) Regulations, 2011") == \
        "SUBSTANTIAL"
    assert x.relation("Disclosures under Reg. 7(1) of SEBI (SAST) Regulations, 1997") == \
        "SUBSTANTIAL"
    assert x.relation("Disclosures under Reg.13(6) of SEBI (Prohibition of Insider "
                      "Trading) Regulations, 1992") == "INSIDER"


def test_key_merges_spellings_and_kind_separates_institutions():
    assert x.key("Siddeshwari Tradex Private Limited") == x.key("SIDDESHWARI TRADEX PVT. LTD.")
    assert x.key("M/s. Jindal Power Ltd") == x.key("Jindal Power Limited")
    assert x.kind("ICICI Prudential Mutual Fund") == "institution"
    assert x.kind("Life Insurance Corporation of India") == "institution"
    assert x.kind("Jindal Power Ltd") == "organisation"
    assert x.kind("Rajendra Gandhi") == "person"


# --------------------------------------------------------------------------- groups
from datetime import date  # noqa: E402

from financial_brain.config import Config  # noqa: E402
from financial_brain.storage.db import Database  # noqa: E402

PLEDGE = "Disclosures under Reg. 31(1) and 31(2) of SEBI (SAST) Regulations, 2011"
EXEMPT = "Disclosures under Reg. 10(6) of SEBI (SAST) Regulations, 2011"
SUBST = "Disclosures under Reg. 29(2) of SEBI (SAST) Regulations, 2011"


@pytest.fixture
def con(tmp_path):
    d = Database(Config(data_root=tmp_path).ensure())
    d.migrate()
    with d.connect() as c:
        yield c


def _file(con, n, day, isin, sub, who):
    head = ("The Exchange has received the disclosure under Regulation 31(1) of SEBI (SAST) "
            f"Regulations, 2011 for {who}")
    con.execute("""INSERT INTO announcements (news_id, source, business_date, scrip_code, isin,
        company, category, subcategory, headline, event_type, materiality, evidence_key,
        observed_at) VALUES (?, 'BSE', ?, ?, ?, ?, 'Company Update', ?, ?, 'X', 'low', 'k',
        NOW())""", [f"n{n}", day, isin[-4:], isin, f"Co {isin[-4:]}", sub, head])


def _history(con):
    k = iter(range(10_000))
    d1, d2 = date(2020, 1, 1), date(2023, 1, 1)
    for isin in ("INE00000A001", "INE00000B001"):          # real group: one holdco, twice each
        _file(con, next(k), d1, isin, PLEDGE, "Alpha Holdings Pvt Ltd")
        _file(con, next(k), d1, isin, EXEMPT, "Alpha Holdings Private Limited")
    _file(con, next(k), d2, "INE00000C001", PLEDGE, "Alpha Holdings Pvt Ltd")  # joins later
    _file(con, next(k), d2, "INE00000C001", PLEDGE, "Alpha Holdings Pvt Ltd")
    for isin in ("INE00000D001", "INE00000E001"):          # common name only: no group
        _file(con, next(k), d1, isin, PLEDGE, "Rajesh Gupta")
    for isin in ("INE00000F001", "INE00000G001"):          # one-off org filing: no group
        _file(con, next(k), d1, isin, PLEDGE, "Beta Traders Pvt Ltd")
    for isin in ("INE00000A001", "INE00000D001", "INE00000F001"):   # a fund: never bridges
        _file(con, next(k), d1, isin, SUBST, "Big Mutual Fund")
        _file(con, next(k), d1, isin, PLEDGE, "Big Mutual Fund")


def test_groups_need_corroborated_promoter_links(con):
    from financial_brain.graph import build
    _history(con)
    stats = build.build(con)
    gs = build.groups(con)
    assert stats["with_filer"] == 16
    assert [g["members"] for g in gs] == [["INE00000A001", "INE00000B001", "INE00000C001"]]
    assert gs[0]["anchor"] == "Alpha Holdings Pvt Ltd"
    assert con.execute("SELECT COUNT(*) FROM promoter_groups").fetchone()[0] == 3


def test_groups_are_point_in_time(con):
    from financial_brain.graph import build
    _history(con)
    build.extract_filings(con)
    early = build.groups(con, as_of=date(2021, 1, 1))
    assert [g["members"] for g in early] == [["INE00000A001", "INE00000B001"]]


def test_profile_traces_to_filings(con):
    from financial_brain.graph import build
    _history(con)
    build.build(con)
    p = build.profile(con, "INE00000C001", as_of=date(2023, 6, 1))
    assert p["pledge_filings_recent"] == 2 and p["pledge_filers_recent"] == 1
    assert p["filers"][0]["filer"] == "Alpha Holdings Pvt Ltd"
    assert all(n.startswith("n") for n in p["filers"][0]["news_ids"])
    assert p["group"]["anchor"] == "Alpha Holdings Pvt Ltd"
