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
