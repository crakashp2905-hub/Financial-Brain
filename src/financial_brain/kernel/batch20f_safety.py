"""Safety and data checks (FB-0623, FB-0642, FB-0645, FB-0646).

* ``parse_bar_row`` is strict: exactly five fields, numeric values, and high >= low. Malformed rows are refused.
* ``scan_secrets`` looks for key-like strings in text and returns the labels it found.
* ``may_send_live`` is False for every actor. External orders are disabled by configuration, so no agent or script can
  send to a live broker.
* ``negative_equity_protection`` floors equity at zero and flags a liability instead of reporting negative equity.
"""
from __future__ import annotations

import re

SECRET_PATTERNS = {
    "AWS_ACCESS_KEY": re.compile(r"AKIA[0-9A-Z]{16}"),
    "GENERIC_API_KEY": re.compile(r"""api[_-]?key\s*=\s*['"][^'"]{8,}['"]""", re.IGNORECASE),
}


def parse_bar_row(row: str) -> dict[str, float]:
    parts = row.split(",")
    if len(parts) != 5:
        raise ValueError(f"expected 5 fields, got {len(parts)}")
    try:
        o, h, lo, c, v = (float(p) for p in parts)
    except ValueError as exc:
        raise ValueError("non-numeric field") from exc
    if h < lo:
        raise ValueError("high below low")
    if v < 0:
        raise ValueError("negative volume")
    return {"open": o, "high": h, "low": lo, "close": c, "volume": v}


def scan_secrets(text: str) -> list[str]:
    return sorted(label for label, pattern in SECRET_PATTERNS.items() if pattern.search(text))


def may_send_live(actor: str) -> bool:
    return False


def negative_equity_protection(equity: float) -> dict[str, float | str | None]:
    if equity < 0:
        return {"equity": 0.0, "flag": "LIABILITY", "liability": -equity}
    return {"equity": equity, "flag": None, "liability": None}
