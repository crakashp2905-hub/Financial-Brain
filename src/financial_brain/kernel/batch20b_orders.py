"""Order handling (FB-0207, FB-0213, FB-0216, FB-0224, FB-0225).

* ``ticket_missing`` lists fields a ticket lacks for its asset class. Extra fields are not an error.
* ``negotiate`` returns whether a venue supports every capability a request needs, and which are missing.
* ``failover_plan`` lists the actions when a multi-leg order partly fills: filled legs are unwound in reverse order.
* ``decision_record`` builds a pre-trade record. It refuses a record with no alternatives considered.
* ``reason_counts`` counts failures by reason code. Unknown reason codes are refused.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

REQUIRED_FIELDS = {
    "EQUITY": {"symbol", "quantity", "side"},
    "FUTURE": {"symbol", "quantity", "side", "expiry"},
    "OPTION": {"symbol", "quantity", "side", "strike", "kind", "expiry"},
    "FX": {"pair", "quantity", "side"},
}
REASON_CODES = {"NO_MARGIN", "PRICE_BAND", "LOT_SIZE", "VENUE_REJECT", "STALE_DATA"}


def ticket_missing(asset_class: str, fields: Mapping[str, object]) -> list[str]:
    if asset_class not in REQUIRED_FIELDS:
        raise KeyError(f"unknown asset class {asset_class!r}")
    return sorted(REQUIRED_FIELDS[asset_class] - set(fields))


def negotiate(venue_capabilities: set[str], required: set[str]) -> tuple[bool, set[str]]:
    missing = required - venue_capabilities
    return not missing, missing


def failover_plan(legs: Sequence[str], filled: Mapping[str, bool]) -> list[str]:
    if all(filled.get(leg, False) for leg in legs):
        return []
    if not any(filled.get(leg, False) for leg in legs):
        return ["CANCEL_ALL"]
    actions = [f"CANCEL {leg}" for leg in legs if not filled.get(leg, False)]
    actions += [f"UNWIND {leg}" for leg in reversed(legs) if filled.get(leg, False)]
    return actions


def decision_record(symbol: str, chosen: str, alternatives: Sequence[tuple[str, str]]) -> dict[str, object]:
    if not alternatives:
        raise ValueError("a decision record needs at least one alternative considered")
    return {
        "symbol": symbol,
        "chosen": chosen,
        "alternatives": [{"name": n, "rejected_because": r} for n, r in alternatives],
    }


def reason_counts(reasons: Sequence[str]) -> dict[str, int]:
    unknown = set(reasons) - REASON_CODES
    if unknown:
        raise ValueError(f"unknown reason codes {sorted(unknown)}")
    return dict(Counter(reasons))
