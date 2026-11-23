"""Event-aware MF total-return path from dated, source-attributed NAVs and events."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date


class TotalReturnError(ValueError):
    pass


_HASH = re.compile(r"^[0-9a-f]{64}$")


def total_return_path(nav_rows: list[dict], events: list[dict]) -> list[dict]:
    """Simulate one initial unit, retaining payouts as cash unless explicitly reinvested.

    Event types: ``IDCW_CASH``, ``IDCW_REINVEST``, ``SPLIT``, ``MERGER``.
    A merger supplies successor scheme and unit conversion ratio; both NAVs must exist
    on the effective date. This output is an accounting path, not an investment signal.
    """
    nav: dict[tuple[date, str], float] = {}
    for row in nav_rows:
        day, code, value = row.get("date"), str(row.get("scheme_code", "")), row.get("nav")
        if not isinstance(day, date) or not code or value is None or float(value) <= 0:
            raise TotalReturnError("NAV rows require date, scheme_code and positive NAV")
        key = (day, code)
        if key in nav:
            raise TotalReturnError(f"duplicate NAV for {code} on {day}")
        nav[key] = float(value)
    by_day: dict[date, list[dict]] = defaultdict(list)
    for event in events:
        day = event.get("effective_on")
        kind = str(event.get("event_type", "")).upper()
        if not isinstance(day, date) or kind not in {"IDCW_CASH", "IDCW_REINVEST", "SPLIT", "MERGER"}:
            raise TotalReturnError("event requires effective_on and a supported event_type")
        if not _HASH.fullmatch(str(event.get("source_sha256", "")).lower()):
            raise TotalReturnError("every event requires source SHA-256")
        by_day[day].append(event)
    dates = sorted({day for day, _ in nav})
    if not dates:
        raise TotalReturnError("no NAV observations")
    if any(day not in dates for day in by_day):
        raise TotalReturnError("event effective date must have a NAV observation")
    scheme = next(code for d, code in nav if d == dates[0])
    units, cash, prior_value = 1.0, 0.0, None
    output = []
    for day in dates:
        if (day, scheme) not in nav:
            raise TotalReturnError(f"NAV missing for active scheme {scheme} on {day}")
        price = nav[(day, scheme)]
        day_events = by_day.get(day, [])
        sequences = [int(item.get("event_sequence", 0)) for item in day_events]
        if len(sequences) != len(set(sequences)):
            raise TotalReturnError("same-day events require unique event_sequence values")
        for event in sorted(day_events, key=lambda x: int(x.get("event_sequence", 0))):
            if str(event.get("scheme_code", scheme)) != scheme:
                raise TotalReturnError("event scheme does not match active holding")
            kind = event["event_type"].upper()
            if kind.startswith("IDCW_"):
                amount = float(event.get("cash_per_unit", -1))
                if amount < 0:
                    raise TotalReturnError("IDCW event requires non-negative cash_per_unit")
                distribution = units * amount
                if kind == "IDCW_CASH":
                    cash += distribution
                else:
                    reinvest_nav = float(event.get("reinvest_nav", 0))
                    if reinvest_nav <= 0:
                        raise TotalReturnError("IDCW_REINVEST requires positive reinvest_nav")
                    units += distribution / reinvest_nav
            elif kind == "SPLIT":
                ratio = float(event.get("units_multiplier", 0))
                if ratio <= 0:
                    raise TotalReturnError("SPLIT requires positive units_multiplier")
                units *= ratio
            else:
                successor = str(event.get("successor_scheme_code", ""))
                ratio = float(event.get("successor_units_per_unit", 0))
                if not successor or ratio <= 0 or (day, successor) not in nav:
                    raise TotalReturnError("MERGER needs successor identity, conversion ratio and NAV")
                units *= ratio
                scheme = successor
                price = nav[(day, scheme)]
        value = units * price + cash
        ret = None if prior_value is None else value / prior_value - 1.0
        output.append({"date": day, "scheme_code": scheme, "units": units,
                       "cash_balance": cash, "nav": price, "wealth": value,
                       "return": ret})
        prior_value = value
    return output
