"""A compliance certificate is not an insider trade.

The insider hypothesis was rejected at t = -0.35, and its pre-registration predicted the
failure mode: "this counts disclosures without reading their direction". That was true and
understated. Counting what the feature actually held showed a third of it reports no
dealing at all - the PIT regulations number a quarterly compliance certificate 7(3) and a
code of conduct alongside the disclosures of real trades, and the rule matching `reg 7(`
swallowed all of them.

These tests pin the separation, because it is the kind of thing that silently reverts.
"""
from __future__ import annotations

import pytest

from financial_brain.events.classify import classify

CAT = "Insider Trading / SAST"


@pytest.mark.parametrize("sub", [
    "Reg. 7(3) - Compliance Certificate (RTA & Compliance Officer)",
    "Reg.7(3) Compliance Certificate",
    "Code of Conduct under SEBI (PIT) Regulations, 2015",
])
def test_filings_that_report_no_dealing_are_compliance(sub):
    kind, materiality, _ = classify(CAT, sub, "", "")
    assert kind == "COMPLIANCE"
    assert materiality == "low"


@pytest.mark.parametrize("sub", [
    "Disclosures under Reg.13(6) of SEBI (Prohibition of Insider Trading) Regulations, 1992",
    "Disclosures under Reg.13(4), 13(4A) of SEBI (Prohibition of Insider Trading) Regulations, 1992",
    "Trading Plan under SEBI (PIT) Regulations, 2015",
])
def test_disclosures_of_dealing_remain_insider_disclosures(sub):
    """A trading plan is a disclosed intention to deal, so it stays in - unlike a
    certificate saying the register was maintained."""
    kind, _, _ = classify(CAT, sub, "", "")
    assert kind == "INSIDER_DISCLOSURE"


def test_substantial_acquisitions_are_unaffected():
    kind, _, _ = classify(CAT, "Disclosures under Reg. 29(2) of SEBI (SAST) "
                               "Regulations, 2011", "", "")
    assert kind == "SUBSTANTIAL_ACQUISITION"


def test_the_compliance_rule_is_checked_before_the_insider_rule():
    """Order is the whole mechanism: `reg 7(` matches `Reg. 7(3)` too, so a rule that
    ran afterwards would never see these filings."""
    from financial_brain.events import classify as mod
    pats = [pat for pat, _, _ in mod._BY_SUBCATEGORY]
    cert = next(i for i, p in enumerate(pats) if "compliance certificate" in p)
    insider = next(i for i, p in enumerate(pats)
                   if "insider trading" in p and "compliance certificate" not in p)
    assert cert < insider
