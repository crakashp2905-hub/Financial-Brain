"""Money in Indian filings, parsed deterministically (C11).

A filing says the thing the headline leaves out: *how much*. "Receipt of order" is not
news; "order worth Rs. 1,451 Crores" is. Indian filings write money in ways that break
naive parsers:

    Rs. 1,451 Crores            crore = 10^7, and the word may be Cr/Cr./Crs/Crores
    Rs 3,68,66,245.58           Indian digit grouping - commas are NOT thousands
    ₹ 22.12 Mn                  mixed Indian and international magnitudes
    USD 1,515,676.25            a foreign currency, which must not be added to rupees
    Rs. 150 Lakh                lakh = 10^5

Everything is normalised to (value, currency) with the original text kept, because a
number whose provenance is lost is worth less than no number. Foreign currencies are
returned as-is - never silently converted, since an FX rate is a claim of its own and
this module has no business inventing one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAGNITUDES = [
    (r"crores?|crs?\.?|cr\.?", 10 ** 7),
    (r"lakhs?|lacs?|lakh|lac", 10 ** 5),
    (r"billions?|bn\.?", 10 ** 9),
    (r"millions?|mn\.?|mio", 10 ** 6),
    (r"thousands?|k", 10 ** 3),
]
CURRENCIES = {"rs": "INR", "rs.": "INR", "inr": "INR", "₹": "INR", "rupees": "INR",
              "usd": "USD", "us$": "USD", "$": "USD", "eur": "EUR", "€": "EUR",
              "gbp": "GBP", "£": "GBP", "jpy": "JPY"}

_CUR = r"(?:Rs\.?|INR|₹|Rupees|USD|US\$|\$|EUR|€|GBP|£|JPY)"
_NUM = r"\d{1,3}(?:[,\s]\d{2,3})*(?:\.\d+)?|\d+(?:\.\d+)?"
_MAG = "|".join(m for m, _ in MAGNITUDES)

# Indian digit grouping is distinctive: 3,68,66,245.58 - groups of two, then a final
# three. Filings often write the figure bare and name the currency just after it, in
# words: "amounting 3,68,66,245.58 (INR Three Crore Sixty Eight Lakh ...)".
_INDIAN = r"\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d+)?"
NEARBY = 40          # characters either side in which a currency word still counts

# "Rs. 1,451 Crores" and "1,451 Crores (Rupees ...)" - currency before or after the number
MONEY = re.compile(
    rf"(?P<cur>{_CUR})\s*(?P<num>{_NUM})\s*(?P<mag>{_MAG})?(?![\w])"
    rf"|(?P<num2>{_NUM})\s*(?P<mag2>{_MAG})\s*(?P<cur2>{_CUR})?(?![\w])"
    rf"|(?P<num3>{_INDIAN})",
    re.I)


@dataclass
class Amount:
    value: float            # in the smallest unit of its currency (rupees, dollars...)
    currency: str
    raw: str                # exactly as written in the document
    start: int
    end: int

    def crore(self) -> float:
        return self.value / 10 ** 7


def _magnitude(word: str | None) -> int:
    if not word:
        return 1
    w = word.strip().lower()
    for pattern, mult in MAGNITUDES:
        if re.fullmatch(pattern, w, re.I):
            return mult
    return 1


def _digits(num: str) -> float | None:
    """Indian grouping means commas cannot be read as thousands separators; they are
    separators of *some* grouping, so they are simply removed."""
    cleaned = num.replace(",", "").replace(" ", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def find(text: str, *, min_value: float = 1000.0) -> list[Amount]:
    """Every money amount in the text, largest-first.

    ``min_value`` drops the noise that fills filings - "Regulation 30", "Rs 10 per
    share" is kept only because per-share amounts are handled by the caller, while bare
    small numbers are not money worth a claim.
    """
    out: list[Amount] = []
    for m in MONEY.finditer(text or ""):
        num = m.group("num") or m.group("num2") or m.group("num3")
        value = _digits(num)
        if value is None:
            continue
        value *= _magnitude(m.group("mag") or m.group("mag2"))
        cur_raw = (m.group("cur") or m.group("cur2") or "").strip().lower()
        if m.group("num3") and not cur_raw:
            # A bare Indian-grouped figure counts as money only if a currency is named
            # close by; otherwise it is as likely a phone number or a registration id.
            window = text[max(0, m.start() - NEARBY):m.end() + NEARBY]
            if not re.search(_CUR, window, re.I):
                continue
            cur_raw = (re.search(_CUR, window, re.I).group(0)).strip().lower()
        cur_raw = cur_raw or "rs"
        currency = CURRENCIES.get(cur_raw, CURRENCIES.get(cur_raw.rstrip("."), "INR"))
        if value < min_value:
            continue
        out.append(Amount(value=value, currency=currency, raw=m.group(0).strip(),
                          start=m.start(), end=m.end()))
    return sorted(out, key=lambda a: -a.value)


def largest(text: str, *, currency: str = "INR", **kw) -> Amount | None:
    """The biggest amount in one currency - usually the headline number of a filing."""
    hits = [a for a in find(text, **kw) if a.currency == currency]
    return hits[0] if hits else None
