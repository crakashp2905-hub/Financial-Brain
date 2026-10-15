"""Direction of a SAST Regulation 29 disclosure: did the filer's holding go up or down?

SEBI's prescribed format (Part A) states the holding **before** and **after** the transaction, each closed by an
``e) Total (a+b+c)`` row with a number of shares and a percentage of the voting capital. The direction is the sign of
after minus before, read from the form itself, and **checked against the cover letter** ("acquisition of 38,739
equity shares", "sale of 25,000 shares") before it is trusted - the regulation's own name ("Substantial Acquisition of
Shares") is in every letter whatever happened, so only a verb followed by a share count counts. The letter also says
whether this was an **inter-se transfer** among promoter-group members (shares moving between related holders, no
outside view), which is flagged.

    read(text) -> {"before_pct", "after_pct", "change_pct", "direction": +1 | -1 | 0 | None, "inter_se": bool,
                   "why": str}
    read_pdf(payload) -> read(...) plus "letter_direction" and "confidence": high | form_only | conflict | ...

``None`` means the form could not be read with confidence; precision over recall, as for the results parser.
"""
from __future__ import annotations

import re

#: The label only; the text after each label (up to the next one, at most 160 characters) is its row. A greedy
#: capture swallowed the next label when totals sat close together and read three totals as one.
TOTAL = re.compile(r"e\)\s*Total\s*\(?\s*a\s*\+\s*b\s*\+\s*c\s*(?:\+\s*d)?\s*\)?", re.I)
NUM = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{2,3})+|\d+)(?![\d,])")
PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
INTER_SE = re.compile(r"inter[\s-]*se", re.I)
NIL = re.compile(r"\bnil\b|\bnone\b|^\s*-\s*$", re.I)
FORM_START = re.compile(r"Format for disclosures?|Part\s*-?\s*A\b", re.I)
#: What the cover letter says was done. A verb followed by a share count; the regulation's name never is.
LETTER_BUY = re.compile(r"\b(?:acquisition|purchase|acquired|bought|allotment)\s+of\s+[\d,]{3,}", re.I)
LETTER_SELL = re.compile(r"\b(?:sale|disposal|sold|disposed\s+off?)\s+of\s+[\d,]{3,}", re.I)


def _total(segment: str) -> tuple[float | None, float | None]:
    """(shares, percent) from the text right after an ``e) Total`` label. NIL reads as zero."""
    seg = segment.replace("\n", " ")
    pct = PCT.search(seg)
    nums = [n for n in NUM.findall(seg[:pct.start()] if pct else seg)]
    shares = float(nums[0].replace(",", "")) if nums else None
    if pct is None and NIL.search(seg[:40]):
        return 0.0, 0.0
    return shares, float(pct.group(1)) if pct else None


def _letter(t: str) -> str:
    """The cover letter: the text before the prescribed format begins (the format itself lists "inter-se transfer"
    among its example modes on every form)."""
    form = FORM_START.search(t)
    return t[:form.start()] if form else t[:2000]


def read(text: str) -> dict:
    t = re.sub(r"[ \t]+", " ", text or "")
    totals = [_total(seg[:160]) for seg in TOTAL.split(t)[1:]]
    out = {"before_pct": None, "after_pct": None, "change_pct": None, "direction": None,
           "inter_se": bool(INTER_SE.search(_letter(t))), "why": ""}
    if len(totals) < 2:
        out["why"] = f"{len(totals)} total row(s) found"
        return out
    before, after = totals[0], totals[-1]                # Part A: before ... (transaction) ... after
    if before[1] is None or after[1] is None:
        # Share counts alone were tried and read the wrong direction in the pilot (numbers from neighbouring cells
        # run together in extracted text), so a form without both percentages is left unread.
        out["why"] = "totals without a readable percentage"
        return out
    out["before_pct"], out["after_pct"] = before[1], after[1]
    out["change_pct"] = round(after[1] - before[1], 4)
    out["direction"] = (out["change_pct"] > 0) - (out["change_pct"] < 0)
    out["why"] = "percent before and after"
    return out


def letter_direction(text: str) -> int | None:
    letter = _letter(re.sub(r"\s+", " ", text or ""))
    buy, sell = bool(LETTER_BUY.search(letter)), bool(LETTER_SELL.search(letter))
    return 1 if buy and not sell else -1 if sell and not buy else None


def layout_text(payload: bytes, *, max_pages: int = 6, y_tol: float = 3.0) -> str:
    """The PDF's words regrouped into visual rows (by vertical position, then left to right), one row a line.

    Plain extraction emits a table column by column, so a ``Total`` label and its numbers end up far apart; read by
    row, each total keeps its own shares and percentages."""
    import fitz
    doc = fitz.open(stream=payload, filetype="pdf")
    lines = []
    for page in doc[:max_pages]:
        words = sorted(page.get_text("words"), key=lambda w: ((w[1] + w[3]) / 2, w[0]))
        row, row_y = [], None
        for w in words:
            yc = (w[1] + w[3]) / 2
            if row_y is not None and abs(yc - row_y) > y_tol:
                lines.append(" ".join(x[4] for x in sorted(row, key=lambda x: x[0])))
                row = []
            row.append(w)
            row_y = yc if row_y is None else (row_y + yc) / 2
        if row:
            lines.append(" ".join(x[4] for x in sorted(row, key=lambda x: x[0])))
    return "\n".join(lines)


def read_pdf(payload: bytes) -> dict:
    """Direction from the form (layout-ordered text, then plain text), **checked against the cover letter**.

    In the 2026-10-14 pilot one confident plain-text read was wrong (a purchase read as a sale: the last percentage
    found was the quantity acquired, not the holding after). So a form direction is accepted ("high") only when the
    letter states the same direction; kept as "form_only" when the letter is silent; dropped as "conflict" when they
    disagree. A letter that states a direction the form cannot confirm is "letter_only"."""
    from .extract import text_of
    layout = layout_text(payload)
    plain, _ = text_of(payload, max_pages=6)
    letter = letter_direction(layout)
    if letter is None:
        letter = letter_direction(plain)
    best = None
    for text, how in ((layout, "layout"), (plain, "plain text")):
        r = read(text)
        if r["direction"] in (1, -1):
            r["why"] += f" ({how})"
            if best is None or (letter is not None and r["direction"] == letter and best["direction"] != letter):
                best = r
    r = best or read(layout)
    r["letter_direction"] = letter
    if r["direction"] in (1, -1):
        r["confidence"] = "high" if letter == r["direction"] else "conflict" if letter is not None else "form_only"
    else:
        r["confidence"] = "letter_only" if letter is not None else "none"
    if r["confidence"] == "conflict":
        r["direction"] = None
    return r
