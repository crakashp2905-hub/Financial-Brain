"""Read-only agent tools (FB-0500, FB-0501, FB-0511, FB-0513, FB-0515, FB-0519, FB-0541).

* ``ReadOnlyStore`` returns copies and refuses writes. The agent can read, never change, the data it is given.
* ``classify_intent`` maps a request onto ASK, RESEARCH, COMPARE, SCREEN or PAPER_DRAFT. Anything else is UNKNOWN.
* ``insufficient_data_message`` states what is missing and speculates about nothing.
* ``groundedness`` is the share of numbers in an answer that appear in its source text.
* ``fallback_answer`` is a deterministic, rule-based answer for a request; it never calls a model.
* ``claim`` attaches one of the claim kinds to a statement. Unknown kinds are refused.
* ``lock_ok`` checks that every dependency in a requirements list is pinned to an exact version.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

INTENT_RULES: tuple[tuple[str, str], ...] = (
    ("compare", "COMPARE"),
    ("screen", "SCREEN"),
    ("paper", "PAPER_DRAFT"),
    ("draft", "PAPER_DRAFT"),
    ("research", "RESEARCH"),
    ("hypothesis", "RESEARCH"),
    ("why", "ASK"),
    ("what", "ASK"),
    ("how", "ASK"),
    ("explain", "ASK"),
)

FALLBACK_TEMPLATES: dict[str, str] = {
    "ASK": "Answer from the stored records only; cite each source.",
    "RESEARCH": "Check the trial inventory for prior tests before proposing a new hypothesis.",
    "COMPARE": "Compare the named items on the same window and fee assumptions.",
    "SCREEN": "Apply the screen rules to the local universe and list the matches.",
    "PAPER_DRAFT": "Draft a paper plan only; nothing is submitted.",
    "UNKNOWN": "Request not understood; no answer is given.",
}


class ReadOnlyStore:
    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = copy.deepcopy(dict(data))

    def get(self, key: str) -> Any:
        return copy.deepcopy(self._data[key])

    def set(self, key: str, value: Any) -> None:
        raise PermissionError("agent store is read-only")


def classify_intent(text: str) -> str:
    lowered = text.lower()
    for word, intent in INTENT_RULES:
        if word in lowered:
            return intent
    return "UNKNOWN"


def insufficient_data_message(needed: int, have: int) -> str:
    return f"Insufficient data: need {needed} observations, have {have}."


def groundedness(claimed_numbers: Sequence[str], source_text: str) -> float:
    if not claimed_numbers:
        return 1.0
    hits = sum(1 for n in claimed_numbers if n in source_text)
    return hits / len(claimed_numbers)


def fallback_answer(text: str) -> str:
    return FALLBACK_TEMPLATES[classify_intent(text)]


class ClaimKind(Enum):
    HYPOTHESIS = "HYPOTHESIS"
    HISTORICAL = "HISTORICAL"
    PAPER = "PAPER"
    VERIFIED = "VERIFIED"


def claim(text: str, kind: str) -> dict[str, str]:
    try:
        ck = ClaimKind(kind)
    except ValueError as exc:
        raise ValueError(f"unknown claim kind {kind!r}") from exc
    return {"text": text, "kind": ck.value}


def lock_ok(lines: Sequence[str]) -> bool:
    pinned = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    if not pinned:
        return False
    return all("==" in ln and ">=" not in ln and "~=" not in ln for ln in pinned)
