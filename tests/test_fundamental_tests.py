"""The amendment-4 fundamentals trials and the insider XBRL parser, offline."""
import numpy as np
import pandas as pd

from financial_brain.evaluation import fundamental_tests as FT
from financial_brain.ingest import insider_xbrl as IX


def _st(pats):
    q = pd.period_range("2019Q1", periods=len(pats), freq="Q")
    return pd.DataFrame({"lineage": "L1", "period_end": q.end_time.normalize(), "pat": pats,
                         "filed_at": q.end_time.normalize() + pd.Timedelta(days=40), "revenue": 100.0})


def test_sue_needs_six_prior_differences_and_scales_by_their_sd():
    rng = np.random.default_rng(0)
    pats = list(10 + rng.normal(0, 1, 15))
    s = FT.sue_table(_st(pats))
    # differences exist from the 5th quarter; the first SUE needs six of them before it: quarter index 10
    assert s["period"].min() == pd.Period("2019Q1", "Q") + 10
    last = s.iloc[-1]
    d = pd.Series(pats).diff(4)
    expect = d.iloc[14] / d.iloc[6:14].std(ddof=1)
    assert abs(last["sue"] - expect) < 1e-9


DOC = b"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:in-bse-co="http://www.bseindia.com/xbrl/co/2017-09-15/in-bse-co">
<xbrli:context id="MainI"/><xbrli:context id="Disclosure1"/><xbrli:context id="Disclosure2"/>
<in-bse-co:Symbol contextRef="MainI">TEXMOPIPES</in-bse-co:Symbol>
<in-bse-co:ISINCode contextRef="MainI">INE141K01013</in-bse-co:ISINCode>
<in-bse-co:RevisedFilling contextRef="MainI">false</in-bse-co:RevisedFilling>
<in-bse-co:CategoryOfPerson contextRef="Disclosure1">Promoter and Director</in-bse-co:CategoryOfPerson>
<in-bse-co:SecuritiesAcquiredOrDisposedTransactionType contextRef="Disclosure1">Buy</in-bse-co:SecuritiesAcquiredOrDisposedTransactionType>
<in-bse-co:SecuritiesAcquiredOrDisposedValueOfSecurity contextRef="Disclosure1" unitRef="INR">45650000</in-bse-co:SecuritiesAcquiredOrDisposedValueOfSecurity>
<in-bse-co:DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyFromDate contextRef="Disclosure1">2026-10-13</in-bse-co:DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyFromDate>
<in-bse-co:SecuritiesAcquiredOrDisposedTransactionType contextRef="Disclosure2">Sell</in-bse-co:SecuritiesAcquiredOrDisposedTransactionType>
<in-bse-co:DateOfIntimationToCompany contextRef="Disclosure2">NA</in-bse-co:DateOfIntimationToCompany>
</xbrli:xbrl>"""


def test_insider_parse_one_row_per_disclosure_with_filing_fields():
    rows = IX.parse(DOC)
    assert [r["side"] for r in rows] == ["Buy", "Sell"]
    assert rows[0]["isin"] == "INE141K01013" and rows[1]["symbol"] == "TEXMOPIPES"
    assert rows[0]["value_inr"] == 45650000.0 and rows[0]["revised"] is False
    assert str(rows[0]["trade_from"]) == "2026-10-13" and rows[1]["intimated_on"] is None
