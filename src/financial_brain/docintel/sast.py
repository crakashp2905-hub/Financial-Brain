"""Direction of a SAST Regulation 29 disclosure: did the filer's holding go up or down?

SEBI's prescribed format (Part A) states the holding **before** and **after** the transaction, each closed by an
``e) Total (a+b+c)`` row with a number of shares and a percentage of the voting capital. The direction is the sign of
after minus before, read from the form itself rather than from the cover letter (cover letters say "acquisition" in
the regulation's own name whatever happened). The cover letter is used for one thing only: an **inter-se transfer**
among promoter-group members moves shares between related holders and says nothing about the outside view, so it is
flagged.

    read(text) -> {"before_pct", "after_pct", "change_pct", "direction": +1 | -1 | 0 | None, "inter_se": bool,
                   "why": str}

``None`` means the form could not be read with confidence (fewer than two totals, or totals that do not parse);
precision over recall, as for the results parser.
"""
from __future__ import annotations

import re

TOTAL = re.compile(r"e\)\s*Total\s*\(?\s*a\s*\+\s*b\s*\+\s*c\s*(?:\+\s*d)?\s*\)?(.{0,160})", re.I | re.S)
NUM = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{2,3})+|\d+)(?![\d,])")
PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
INTER_SE = re.compile(r"inter[\s-]*se", re.I)
NIL = re.compile(r"\bnil\b|\bnone\b|^\s*-\s*$", re.I)


def _total(segment: str) -> tuple[float | None, float | None]:
    """(shares, percent) from the text right after an ``e) Total`` label. NIL reads as zero."""
    seg = segment.replace("\n", " ")
    pct = PCT.search(seg)
    nums = [n for n in NUM.findall(seg[:pct.start()] if pct else seg)]
    shares = float(nums[0].replace(",", "")) if nums else None
    if pct is None and NIL.search(seg[:40]):
        return 0.0, 0.0
    return shares, float(pct.group(1)) if pct else None


def read(text: str) -> dict:
    t = re.sub(r"[ \t]+", " ", text or "")
    totals = [_total(m.group(1)) for m in TOTAL.finditer(t)]
    # The prescribed format itself lists "inter-se transfer" among its example modes, so only the cover letter (the
    # text before the format begins) can say that this transaction was one.
    form = re.search(r"Format for disclosures?|Part\s*-?\s*A\b", t, re.I)
    letter = t[:form.start()] if form else t[:1500]
    out = {"before_pct": None, "after_pct": None, "change_pct": None, "direction": None,
           "inter_se": bool(INTER_SE.search(letter)), "why": ""}
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
