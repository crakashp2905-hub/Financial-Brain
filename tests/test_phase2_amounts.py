"""Money in Indian filings (C11). The headline says "receipt of order"; the document
says how much. These strings are taken from real BSE filings in the archive."""
from __future__ import annotations

import pytest

from financial_brain.docintel import amounts

CRORE = 10 ** 7


@pytest.mark.parametrize("text,value,currency", [
    ("JMC secures new orders of Rs. 1,451 Crores", 1451 * CRORE, "INR"),
    ("Receipt of orders worth about USD 22.12 Mn (approximately Rs 174.6 Crores)",
     174.6 * CRORE, "INR"),
    ("order worth Rs. 161 crores from an International Entity", 161 * CRORE, "INR"),
    ("Export Orders aggregating to Rs. 225 Lakhs", 225 * 10 ** 5, "INR"),
    ("milestone order of INR 85 Million", 85 * 10 ** 6, "INR"),
    ("₹ 16,59,630 Cr.", 1659630 * CRORE, "INR"),
])
def test_real_filing_amounts_parse(text, value, currency):
    a = amounts.largest(text, currency=currency)
    assert a is not None and a.value == pytest.approx(value)


def test_indian_digit_grouping_is_not_thousands():
    """Rs 3,68,66,245.58 is 3.68 crore, not 3.6 billion. Reading the commas as
    thousands separators would overstate it by ~100x."""
    a = amounts.largest("amounting 3,68,66,245.58 (INR Three Crore Sixty Eight Lakh)")
    assert a.value == pytest.approx(36866245.58)
    assert a.crore() == pytest.approx(3.6866245, rel=1e-6)


def test_a_foreign_currency_is_kept_foreign():
    """An FX rate is a claim of its own; this module does not invent one."""
    found = amounts.find("Disclosure of the Receipt of new order of US $ 1515676.25 Approx")
    assert found and found[0].currency == "USD"
    assert found[0].value == pytest.approx(1515676.25)
    assert amounts.largest("US $ 1515676.25", currency="INR") is None


def test_the_largest_rupee_amount_is_the_headline_number():
    text = ("penalty of Rs. 10,000 was paid and the Company received an order of "
            "Rs. 45 Crores from the client")
    a = amounts.largest(text)
    assert a.crore() == pytest.approx(45)


def test_noise_below_the_floor_is_not_money():
    assert amounts.find("Regulation 30 of SEBI LODR 2015, clause 16(g)") == []
    assert amounts.find("Rs 10 per share", min_value=1000) == []


def test_the_original_text_is_kept():
    a = amounts.largest("new orders of Rs. 1,451 Crores")
    assert "1,451" in a.raw and "Crore" in a.raw.title()
