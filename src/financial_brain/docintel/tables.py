"""Reconstructing a table from a PDF page, by position (C11).

A results statement is a table, and PDF has no tables - only text placed at coordinates.
Plain extraction returns every label, then every number, in two separate runs: the labels
"Revenue from Operations / Other Income / Total Income" followed later by
"298,621 4,447 303,068". Pairing those by order is guesswork the moment one cell is
blank.

So text is collected *with its position* and grouped into rows by the y coordinate, the
way a reader's eye does it. Within a row, fragments are ordered by x, the leftmost run
of words is the label and the numbers that follow are its columns - current quarter,
previous quarter, year-ago quarter, full year - in the order the statement prints them.

This also survives the OCR in scanned filings, which mangles letters ("Olher Income")
but rarely moves them.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

Y_TOLERANCE = 2.5       # points; text within this of another fragment is the same row
NUMBER = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$")


@dataclass
class Row:
    label: str
    numbers: list[float] = field(default_factory=list)
    raw_cells: list[str] = field(default_factory=list)
    y: float = 0.0


def _fragments(page) -> list[tuple[float, float, str]]:
    """(x, y, text) for every text fragment pypdf can place on the page."""
    out: list[tuple[float, float, str]] = []

    def visitor(text, cm, tm, font_dict, font_size):
        if text and text.strip():
            out.append((tm[4], tm[5], text))

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    page.extract_text(visitor_text=visitor)
    return out


def _number(cell: str) -> float | None:
    """A statement writes negatives in brackets: (1,234) is -1234."""
    s = cell.strip().replace(" ", "")
    if not NUMBER.match(s):
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "")
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value


def rows(page) -> list[Row]:
    """The page as labelled rows of numbers, top to bottom."""
    frags = _fragments(page)
    if not frags:
        return []
    buckets: dict[float, list[tuple[float, str]]] = {}
    for x, y, text in frags:
        key = next((k for k in buckets if abs(k - y) <= Y_TOLERANCE), y)
        buckets.setdefault(key, []).append((x, text))

    out: list[Row] = []
    for y in sorted(buckets, reverse=True):          # PDF y grows upwards
        cells = [t for _, t in sorted(buckets[y], key=lambda c: c[0])]
        label_parts, numbers, raw = [], [], []
        for cell in cells:
            value = _number(cell)
            if value is None:
                if not numbers:                      # label text precedes the figures
                    label_parts.append(cell.strip())
            else:
                numbers.append(value)
                raw.append(cell.strip())
        label = " ".join(" ".join(label_parts).split())
        if label or numbers:
            out.append(Row(label=label, numbers=numbers, raw_cells=raw, y=y))
    return out
