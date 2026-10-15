"""Integrated Filing: SEBI in-capmkt XBRL (any taxonomy version) parses like the older results XBRL."""
from financial_brain.docintel import xbrl_results as X
from financial_brain.ingest import integrated_filing as IF

DOC = b"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:in-capmkt="http://www.sebi.gov.in/xbrl/2025-03-31/in-capmkt">
<xbrli:context id="OneD"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier></xbrli:entity>
<xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-03-31</xbrli:endDate></xbrli:period></xbrli:context>
<in-capmkt:RevenueFromOperations contextRef="OneD" unitRef="INR" decimals="-5">1000</in-capmkt:RevenueFromOperations>
<in-capmkt:ProfitLossForPeriod contextRef="OneD" unitRef="INR" decimals="-5">100</in-capmkt:ProfitLossForPeriod>
<in-capmkt:NameOfTheCompany contextRef="OneD">X Ltd</in-capmkt:NameOfTheCompany>
</xbrli:xbrl>"""


def test_any_sebi_taxonomy_version_parses():
    inst = X.parse(DOC)
    assert inst.canonical()["revenue"] == 1000 and inst.canonical()["pat"] == 100
    assert inst.period == ("2025-01-01", "2025-03-31")


def test_row_mapping_carries_time_and_revision():
    r = {"seq_Id": "201460", "symbol": "TATACOMM", "cmName": "Tata", "qe_Date": "30-SEP-2026",
         "consolidated": "Consolidated", "type_Sub": "Revision", "audited": "Un-Audited",
         "broadcast_Date": "15-Oct-2026 17:21:17", "creation_Date": "15-Oct-2026 17:21:34", "xbrl": "u.xml"}
    row = IF._row(r, "INE151A01013", X.parse(DOC))
    assert row["seqNumber"] == "IF201460" and row["relatingTo"] == "Revision"
    assert row["fromDate"] == "01-Jan-2025" and row["exchdisstime"].startswith("15-Oct-2026")


def test_months_cover_the_range():
    from datetime import date
    m = list(IF._months(date(2025, 4, 15), date(2025, 6, 2)))
    assert m[0] == (date(2025, 4, 15), date(2025, 4, 30)) and m[-1] == (date(2025, 6, 1), date(2025, 6, 2))
