"""Explanation and retrieval tools (FB-0502, FB-0503, FB-0509, FB-0510, FB-0512, FB-0516, FB-0520).

* ``retrieve_context`` returns only the keys asked for from local stores. An unknown key is refused.
* ``explain_value`` shows a number with its calculation. A value without a calculation is refused.
* ``hypothesis_check`` reports whether a new hypothesis duplicates a prior trial in the inventory.
* ``explain_signal`` explains only signals that are registered. An unregistered name is refused.
* ``explain_decision`` gives a reason for the chosen action and for every alternative. NO_TRADE is allowed.
* ``explain_rejection`` explains a rejected order or risk denial with a known reason code.
* ``export_transcript`` writes messages as JSON lines in the order given, and refuses a message with no role or text.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

REJECTION_CODES = {"NO_MARGIN", "PRICE_BAND", "LOT_SIZE", "VENUE_REJECT", "STALE_DATA", "RISK_LIMIT"}


def retrieve_context(store: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in keys:
        if key not in store:
            raise KeyError(f"no local record {key!r}")
        out[key] = store[key]
    return out


def explain_value(name: str, value: float, calculation: str) -> str:
    if not calculation.strip():
        raise ValueError("every number needs its calculation shown")
    return f"{name} = {value} ({calculation})"


def hypothesis_check(new_name: str, trial_inventory: set[str]) -> str:
    return "DUPLICATE_OF_PRIOR_TRIAL" if new_name in trial_inventory else "NEW"


def explain_signal(name: str, registered: set[str]) -> str:
    if name not in registered:
        raise KeyError(f"signal {name!r} is not registered")
    return f"{name} is a registered signal"


def explain_decision(action: str, chosen_reason: str, alternatives: Mapping[str, str]) -> dict[str, Any]:
    if not chosen_reason.strip():
        raise ValueError("the chosen action needs a reason")
    for name, reason in alternatives.items():
        if not reason.strip():
            raise ValueError(f"alternative {name} needs a reason")
    return {"action": action, "reason": chosen_reason, "alternatives": dict(sorted(alternatives.items()))}


def explain_rejection(reason_code: str, detail: str) -> str:
    if reason_code not in REJECTION_CODES:
        raise ValueError(f"unknown rejection code {reason_code!r}")
    return f"Rejected ({reason_code}): {detail}"


def export_transcript(messages: Sequence[Mapping[str, str]]) -> str:
    lines = []
    for m in messages:
        if not m.get("role") or not m.get("text"):
            raise ValueError("every message needs a role and text")
        lines.append(json.dumps({"role": m["role"], "text": m["text"]}, sort_keys=True))
    return "\n".join(lines)
