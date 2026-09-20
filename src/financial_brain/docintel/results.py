"""As-reported quarterly financials, read from the results PDF a company filed (C11/C03).

This is the dataset that cannot be bought back later. Screener and every other compiler
carries *restated* figures; what a company actually reported on the day, and what it
later restated, is only visible if it is captured as filed. So each row here is stored
with the filing that produced it and is never overwritten - a restatement is a new row,
and a backtest can ask what was knowable on a date rather than what is true now.

Three things make this reliable enough to store:

* **Position, not order.** The table is reconstructed from coordinates (``tables.py``),
  so a label keeps its own figures.
* **Fuzzy labels.** Many Indian results PDFs are scans: "Olher Income", "Value of Sales
  & Setvices". Labels are matched by similarity, not equality.
* **The statement must add up.** Revenue + other income = total income, and total income
  - total expenses = profit before tax, each within a small tolerance. A page that
  fails its own arithmetic is not stored: it means a column was misread, and a wrong
  revenue figure is worse than none.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher

from . import tables

CANONICAL = {
    "revenue": ["revenue from operations", "income from operations",
                "revenue from operation", "total revenue from operations"],
    "other_income": ["other income"],
    "total_income": ["total income", "total revenue"],
    "total_expenses": ["total expenses", "total expenditure"],
    "pbt": ["profit before tax", "profit/(loss) before tax",
            "profit before tax and exceptional items"],
    # OCR turns "Profit" into "Proflt" and filers write the period as "Period/Year", so
    # the variants below are the shapes seen in real filings, matched by similarity.
    "pat": ["profit for the period", "profit after tax", "net profit for the period",
            "profit loss for the period", "profit for the year",
            "net profit loss for the period year", "net profit loss for the period",
            "profit loss for the period year", "profit for the period year"],
    "eps_basic": ["basic", "basic eps", "earnings per share basic", "basic (rs)"],
}
MIN_RATIO = 0.82           # label similarity, generous enough for OCR damage

UNITS = [(r"in\s+cr", 10 ** 7), (r"in\s+lakh|in\s+lac", 10 ** 5),
         (r"in\s+million|in\s+mn", 10 ** 6), (r"in\s+thousand", 10 ** 3)]
BASIS = [("consolidated", r"consolidat"), ("standalone", r"standalone|unconsolidat")]
# The ordinal suffix is whatever the scanner made of it: "30TH" often comes back "301H",
# "3QTH", "30Ih". Anything up to three characters between the day and the month is
# tolerated, because the day and the month name are the parts OCR gets right.
PERIOD = re.compile(
    r"(?:quarter|period|year)\s+ended\s*:?\s*"
    r"(\d{1,2})\s*[a-z0-9']{0,3}\s*,?\s*"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s*,?\s*(\d{4})", re.I)
# Some filers write the month first: "for the period ended September 30, 2025".
PERIOD_MONTH_FIRST = re.compile(
    r"(?:quarter|period|year)\s+ended\s*:?\s*"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s*"
    r"(\d{1,2})\s*[a-z0-9']{0,3}\s*,?\s*(\d{4})", re.I)
# And many write it numerically: "Quarter ended 30.06.2026" / "30-06-2026" / "30/06/2026".
# Nine statements in a 60-filing sample parsed cleanly and were then dropped for want of
# a period, which is the most wasteful way to lose data.
PERIOD_NUMERIC = re.compile(
    r"(?:quarter|period|year)\s+ended\s*:?\s*"
    r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", re.I)
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


@dataclass
class Statement:
    basis: str                       # consolidated | standalone
    period_end: date | None
    unit: int                        # multiplier to rupees
    values: dict[str, float] = field(default_factory=dict)
    checks: dict[str, bool] = field(default_factory=dict)
    page: int = 0

    def ok(self) -> bool:
        """Did the statement pass its own arithmetic, and say something?

        Zero revenue and zero income satisfy any sum trivially - 0 + x = x - so a
        degenerate reading would sail through the checks. A results statement that
        reports nothing at all is a misread, not a quiet quarter.
        """
        if not self.checks or not all(self.checks.values()):
            return False
        return max(abs(self.values.get("revenue") or 0),
                   abs(self.values.get("total_income") or 0)) > 0


def _norm(label: str) -> str:
    """Strip what a statement decorates its labels with, so the words are left.

    A real line reads "13 Net Proflt/(Loss) for the Period/Year (9+12)": a row number,
    OCR damage, and the formula that produced it. Only the words identify the line.
    """
    s = label.lower()
    s = re.sub(r"\((?:refer|see)[^)]*\)", " ", s)      # (Refer note-5)
    s = re.sub(r"\(\s*[0-9+\-/ ]+\s*\)", " ", s)       # (9+12), (3+4), (1-2)
    s = re.sub(r"^\s*\d+\s*[.)\]]?\s*", " ", s)        # leading row number
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(s.split())


def match(label: str) -> str | None:
    """The canonical field this row label means, tolerating OCR damage."""
    norm = _norm(label)
    if not norm or len(norm) < 3:
        return None
    best, best_ratio = None, 0.0
    for field_name, variants in CANONICAL.items():
        for variant in variants:
            ratio = SequenceMatcher(None, norm, variant).ratio()
            if norm.startswith(variant) or variant in norm:
                ratio = max(ratio, 0.95)
            if ratio > best_ratio:
                best, best_ratio = field_name, ratio
    return best if best_ratio >= MIN_RATIO else None


def unit_of(text: str) -> int:
    """"(Rs. in crore, except per share data)" -> 10^7. Defaults to rupees."""
    head = text[:4000].lower()
    for pattern, mult in UNITS:
        if re.search(pattern, head):
            return mult
    return 1


def basis_of(text: str) -> str:
    head = text[:4000].lower()
    for name, pattern in BASIS:
        if re.search(pattern, head):
            return name
    return "unknown"


def period_of(text: str) -> date | None:
    head = text[:4000]
    m = PERIOD.search(head)
    if m:
        day, month, year = int(m.group(1)), MONTHS[m.group(2).lower()[:3]], int(m.group(3))
    elif (m := PERIOD_MONTH_FIRST.search(head)):
        day, month, year = int(m.group(2)), MONTHS[m.group(1).lower()[:3]], int(m.group(3))
    elif (m := PERIOD_NUMERIC.search(head)):
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        year += 2000 if year < 100 else 0          # "26" means 2026, not year 26
        if month > 12:                             # written month-first; swap
            day, month = month, day
    else:
        return None
    try:
        return date(year, month, min(day, 31))
    except ValueError:
        return None


def _check(values: dict[str, float], unit: int) -> dict[str, bool]:
    """The statement's own arithmetic. Tolerance is one reporting unit plus 0.5%, which
    covers the rounding a company does when it prints in crore."""
    out: dict[str, bool] = {}

    def close(a: float, b: float) -> bool:
        return abs(a - b) <= max(1.0, abs(b) * 0.005)

    if {"revenue", "other_income", "total_income"} <= values.keys():
        out["income adds up"] = close(values["revenue"] + values["other_income"],
                                      values["total_income"])
    if {"total_income", "total_expenses", "pbt"} <= values.keys():
        out["profit before tax follows"] = close(
            values["total_income"] - values["total_expenses"], values["pbt"])
    return out


def read_page(page, page_no: int = 0) -> Statement | None:
    """One page of a results PDF, if it holds a statement."""
    rows = tables.rows(page)
    if not rows:
        return None
    text = "\n".join(r.label for r in rows)
    if not re.search(r"revenue|income from operation", text, re.I):
        return None

    values: dict[str, float] = {}
    for row in rows:
        if not row.numbers:
            continue
        field_name = match(row.label)
        if field_name and field_name not in values:
            values[field_name] = row.numbers[0]        # leftmost column = this period
    if "revenue" not in values and "total_income" not in values:
        return None

    unit = unit_of(text)
    st = Statement(basis=basis_of(text), period_end=period_of(text), unit=unit,
                   values=values, page=page_no)
    st.checks = _check(values, unit)
    return st


def read(payload: bytes, *, max_pages: int = 16) -> list[Statement]:
    """Every statement in the document that passes its own arithmetic.

    A results filing usually carries both consolidated and standalone statements; both
    are returned, and the caller stores each under its own basis.
    """
    import io
    import logging

    import pypdf
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = pypdf.PdfReader(io.BytesIO(payload))
    out: list[Statement] = []
    for i, page in enumerate(reader.pages[:max_pages]):
        try:
            st = read_page(page, i)
        except Exception:               # noqa: BLE001 - one bad page is not a failure
            continue
        if st and st.ok():
            out.append(st)
    return out
