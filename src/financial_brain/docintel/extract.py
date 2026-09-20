"""What a filing's own document says (C11).

The announcement row carries a headline; the attachment carries the facts. Some headlines
say nothing at all - "Please refer the Enclosed files." - while the PDF behind them
declares an interim dividend. This module turns those bytes into a small number of
**typed, located** claims, deterministically:

* the document's text (pypdf), page by page;
* the headline money amount, classified by what the surrounding words call it - an order,
  a penalty, a tax demand, money being raised;
* dividend per share, which is small enough to fall under the money floor and so is
  matched on its own terms.

Two rules keep this honest, both learned from the first run against real filings, which
minted an investor deck's "addressable market demand of Rs 65,000 crore" as a *tax
demand*:

1. **A number must be named.** The patterns below require the phrase that names a
   company event ("demand notice", "order worth"), not the topic word alone. An
   unnamed figure is not a claim.
2. **A number must agree with the filing it is in.** The document is the authority on
   *how much*; the exchange's own classification is the authority on *what happened*.
   A tax demand extracted from an ORDER_WIN filing is noise, and is dropped.

Everything returned carries the sentence it came from, so a claim can be read back
against the document rather than trusted. A document whose text cannot be extracted (a
scan) returns no facts and says so, rather than returning zero.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import amounts

KINDS: list[tuple[str, re.Pattern]] = [
    ("penalty", re.compile(r"penalty\s+(?:of|amounting|imposed)|imposed\s+a\s+penalty|"
                           r"fine\s+of|late\s+fee", re.I)),
    # No bare "demand of Rs ...": a deck's "market demand of Rs 65,000 crore" matched it.
    # A tax demand in an Indian filing is always named - a notice, an order, or a raise.
    ("tax_demand", re.compile(r"demand\s+(?:notice|order)|raised\s+a\s+demand|"
                              r"assessment\s+order|show\s+cause\s+notice|tax\s+demand|"
                              r"demand\s+under\s+section", re.I)),
    ("order_value", re.compile(r"(?:order|contract|work\s+order|purchase\s+order|"
                               r"letter\s+of\s+award|\bLoA\b)\s+(?:of|for|worth|valued|"
                               r"amounting|aggregating)|"
                               r"(?:bagged|secured|awarded|received)\s+(?:an?\s+|the\s+)?"
                               r"(?:order|contract|work\s+order|purchase\s+order)", re.I)),
    ("fund_raise", re.compile(r"(?:raise|raising)\s+(?:of\s+)?(?:up\s?to\s+)?"
                              r"(?:Rs|INR|₹)|\bQIP\b|preferential\s+(?:issue|allotment)|"
                              r"rights\s+issue|non[\s-]?convertible\s+debenture", re.I)),
    ("acquisition", re.compile(r"acquisition\s+of|acquire\s+.{0,40}(?:stake|shares|equity)|"
                               r"scheme\s+of\s+(?:merger|amalgamation)", re.I)),
    ("default", re.compile(r"default\s+in\s+(?:payment|repayment)|amount\s+in\s+default|"
                           r"overdue\s+(?:amount|principal)", re.I)),
]
WINDOW = 160        # characters either side of the amount that name it

# Which kinds of number each event type can legitimately carry. **Default deny**: a type
# not listed here mints nothing. A CRISIL release filed as GENERAL put the size of a
# rated debt programme (Rs 1,087.5 crore) through as a "fund raise" while unknown types
# were permissive, and a filing we cannot name is exactly where a confident wrong number
# does the most damage.
EVENT_KINDS: dict[str, set[str]] = {
    "ORDER_WIN": {"order_value"},
    "LEGAL_REGULATORY": {"tax_demand", "penalty", "default", "demand_set_aside"},
    "DIVIDEND": {"dividend_per_share"},
    "FUND_RAISING": {"fund_raise"},
    "ALLOTMENT": {"fund_raise"},
    "ACQUISITION": {"acquisition"},
    "AGREEMENT": {"order_value", "acquisition"},
    "INSOLVENCY": {"default", "tax_demand"},
}

# Words that mean the amount went *away*: an appeal allowed, a demand quashed. Without
# this, "the ITAT set aside the demand of Rs 1.17 crore" reads as a live tax demand -
# the number is right and the direction is the opposite.
RELIEF = re.compile(r"set\s+aside|quash|deleted?\s+the\s+(?:demand|addition)|"
                    r"allowed\s+the\s+appeal|in\s+favour\s+of\s+the\s+company|"
                    r"dropped\s+(?:all\s+)?the\s+proceedings|favourable\s+order", re.I)

DIVIDEND = re.compile(
    r"(?:dividend)[^.]{0,120}?(?:Rs\.?|INR|₹)\s*(\d+(?:\.\d+)?)\s*(?:/-)?\s*"
    r"(?:per|/)\s*(?:equity\s+)?share"
    r"|(?:Rs\.?|INR|₹)\s*(\d+(?:\.\d+)?)\s*(?:/-)?\s*per\s+(?:equity\s+)?share"
    r"[^.]{0,60}?dividend", re.I)


@dataclass
class Fact:
    kind: str
    value: float
    unit: str            # INR, USD, INR_per_share ...
    raw: str             # the figure as the document writes it
    context: str         # the sentence it sits in, trimmed


def text_of(payload: bytes, *, max_pages: int = 12) -> tuple[str, int]:
    """(text, pages). Reads at most ``max_pages``: the facts in an Indian filing are on
    the first page or two, and an annual report is not worth parsing in full here."""
    import io
    import logging

    import pypdf
    # pypdf logs a paragraph per embedded font it cannot fully parse. That is noise
    # about someone else's typography, not about the disclosure we are reading.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = pypdf.PdfReader(io.BytesIO(payload))
    pages = len(reader.pages)
    parts = []
    for page in reader.pages[:max_pages]:
        try:
            parts.append(page.extract_text() or "")
        except Exception:             # noqa: BLE001 - one broken page is not a failure
            continue
    return "\n".join(parts), pages


def _context(text: str, start: int, end: int) -> str:
    left = text.rfind(".", max(0, start - 300), start)
    right = text.find(".", end, end + 300)
    chunk = text[(left + 1 if left != -1 else max(0, start - 160)):
                 (right if right != -1 else min(len(text), end + 160))]
    return " ".join(chunk.split())[:300]


def classify(text: str, amount: amounts.Amount) -> str:
    """What the words around this amount call it; ``amount`` when nothing names it."""
    window = text[max(0, amount.start - WINDOW):amount.end + WINDOW]
    for kind, pattern in KINDS:
        if pattern.search(window):
            if kind in ("tax_demand", "penalty") and RELIEF.search(window):
                return "demand_set_aside"       # the company won; the amount is relief
            return kind
    return "amount"


def admissible(kind: str, event_type: str | None) -> bool:
    """Can a filing of this event type carry this kind of number?"""
    if kind == "amount":
        return False                      # an unnamed number is not a claim
    return kind in EVENT_KINDS.get(event_type or "", set())


def facts(text: str, *, min_value: float = 100_000.0,
          event_type: str | None = None) -> list[Fact]:
    """The few claims worth minting from a filing's document.

    At most one amount per currency: a filing repeats figures in words and digits, in
    tables and with taxes added, and minting every match would bury the one that matters.
    """
    out: list[Fact] = []
    seen: set[str] = set()
    for a in amounts.find(text, min_value=min_value):
        if a.currency in seen:
            continue
        kind = classify(text, a)
        if not admissible(kind, event_type):
            continue
        seen.add(a.currency)
        out.append(Fact(kind=kind, value=a.value, unit=a.currency,
                        raw=" ".join(a.raw.split()),
                        context=_context(text, a.start, a.end)))

    m = DIVIDEND.search(text or "")
    if m and admissible("dividend_per_share", event_type):
        out.append(Fact(kind="dividend_per_share", value=float(m.group(1) or m.group(2)),
                        unit="INR_per_share", raw=" ".join(m.group(0).split())[:60],
                        context=_context(text, m.start(), m.end())))
    return out


def looks_scanned(text: str, pages: int) -> bool:
    """A scan yields almost no characters. Saying so beats reporting "no facts"."""
    return pages > 0 and len(text.strip()) < 40 * pages
