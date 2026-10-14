"""SAST Reg. 29 direction parser: before/after totals from the prescribed format; inter-se only from the letter."""
from financial_brain.docintel import sast

FORM = """Dear Sir, please find the disclosure for {letter}.
Format for disclosures under Regulation 29(2) Part-A- Details of Acquisition / Sale
Mode of acquisition / sale (e.g. open market / off-market / public issue / rights issue / preferential allotment /
inter-se transfer etc). Open market
Before the acquisition / sale under consideration, holding of : a) Shares ... e) Total (a+b+c) 25,000 {before}% {before}%
Details of acquisition/sale ... e) Total (a+b+c) 5,000 0.10% 0.10%
After the acquisition / sale, holding of: a) Shares ... e) Total (a+b+c) 30,000 {after}% {after}%
"""


def test_direction_from_before_and_after():
    assert sast.read(FORM.format(letter="a purchase", before="0.50", after="0.60"))["direction"] == 1
    assert sast.read(FORM.format(letter="a sale", before="0.60", after="0.50"))["direction"] == -1


def test_inter_se_comes_from_the_letter_not_the_template():
    assert not sast.read(FORM.format(letter="a purchase", before="0.5", after="0.6"))["inter_se"]
    assert sast.read(FORM.format(letter="an inter-se transfer among promoters", before="0.5", after="0.6"))["inter_se"]


def test_unreadable_forms_are_left_unread():
    assert sast.read("a scanned page with no text")["direction"] is None
    assert sast.read("e) Total (a+b+c) 25,000 e) Total (a+b+c) 30,000")["direction"] is None   # shares only
