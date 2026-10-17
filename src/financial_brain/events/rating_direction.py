"""Which way a credit-rating announcement points: upgrade, downgrade, reaffirmed, outlook change, watch, new, withdrawn.

Rules on the announcement's own headline and subject (nothing learned). A rating action already has its event type
(``CREDIT_RATING``); this adds the direction that makes it usable as an event. The first matching rule wins, in the
order below, because a headline can say both ("downgraded ... outlook stable").
"""
from __future__ import annotations

import re

RULES = [
    ("withdrawn", re.compile(r"\bwithdraw", re.I)),
    ("downgrade", re.compile(r"\bdown[- ]?grad|\brevised\s+(?:\w+\s+){0,4}downward|\blowered\b", re.I)),
    ("upgrade", re.compile(r"\bup[- ]?grad|\brevised\s+(?:\w+\s+){0,4}upward|\braised\b", re.I)),
    ("watch_negative", re.compile(r"watch\s+with\s+negative|negative\s+watch|rating\s+watch\s+negative", re.I)),
    ("watch_positive", re.compile(r"watch\s+with\s+(?:positive|developing)|positive\s+watch", re.I)),
    ("outlook_negative", re.compile(r"outlook\s+(?:revised\s+|changed\s+)?(?:from\s+\w+\s+)?to\s+negative|negative\s+outlook", re.I)),
    ("outlook_positive", re.compile(r"outlook\s+(?:revised\s+|changed\s+)?(?:from\s+\w+\s+)?to\s+positive|positive\s+outlook", re.I)),
    ("reaffirmed", re.compile(r"reaffirm|re-affirm|retain|maintain", re.I)),
    ("assigned", re.compile(r"\bassign", re.I)),
]
SIGN = {"upgrade": 1, "watch_positive": 1, "outlook_positive": 1, "downgrade": -1, "watch_negative": -1,
        "outlook_negative": -1, "reaffirmed": 0, "assigned": 0, "withdrawn": 0, "unclear": 0}


def direction(text: str) -> str:
    for name, rx in RULES:
        if rx.search(text or ""):
            return name
    return "unclear"
