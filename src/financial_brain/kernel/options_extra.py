"""Option tools (FB-0292, FB-0294, FB-0303, FB-0304, FB-0310).

* ``chain_filter`` filters an option chain by kind, expiry and strike range. Quotes with a crossed market are dropped.
* ``binomial_american`` prices an American option on a Cox-Ross-Rubinstein tree with early exercise.
* ``short_option_margin`` is a simple exchange-style estimate: the larger of two percentages of the underlying, less
  out-of-the-money amount, plus premium. It is a model, not an exchange figure, and is labelled as such.
* ``expiry_events`` lists assignment and exercise at expiry for in-the-money positions.
* ``pin_risk`` flags short options whose strike sits within a tolerance of spot at expiry.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ChainQuote:
    kind: str  # CALL | PUT
    expiry: str
    strike: Decimal
    bid: Decimal
    ask: Decimal
    open_interest: int


def chain_filter(
    chain: Sequence[ChainQuote],
    kind: str | None = None,
    expiry: str | None = None,
    strike_min: Decimal | None = None,
    strike_max: Decimal | None = None,
) -> list[ChainQuote]:
    out: list[ChainQuote] = []
    for q in chain:
        if q.bid > q.ask:
            continue  # crossed market is never shown as tradable
        if kind is not None and q.kind != kind:
            continue
        if expiry is not None and q.expiry != expiry:
            continue
        if strike_min is not None and q.strike < strike_min:
            continue
        if strike_max is not None and q.strike > strike_max:
            continue
        out.append(q)
    return out


def binomial_american(
    spot: float, strike: float, rate: float, sigma: float, years: float, steps: int, kind: str
) -> float:
    if steps < 1 or years <= 0 or sigma <= 0:
        raise ValueError("steps, years and sigma must be positive")
    dt = years / steps
    up = math.exp(sigma * math.sqrt(dt))
    down = 1.0 / up
    disc = math.exp(-rate * dt)
    p = (math.exp(rate * dt) - down) / (up - down)
    if not (0.0 <= p <= 1.0):
        raise ValueError("risk-neutral probability out of range")
    sign = 1.0 if kind == "CALL" else -1.0 if kind == "PUT" else 0.0
    if sign == 0.0:
        raise ValueError("kind must be CALL or PUT")
    values = [max(sign * (spot * up**j * down ** (steps - j) - strike), 0.0) for j in range(steps + 1)]
    for n in range(steps - 1, -1, -1):
        for j in range(n + 1):
            cont = disc * (p * values[j + 1] + (1 - p) * values[j])
            exercise = max(sign * (spot * up**j * down ** (n - j) - strike), 0.0)
            values[j] = max(cont, exercise)
    return values[0]


def short_option_margin(spot: Decimal, strike: Decimal, premium: Decimal, kind: str, quantity: int) -> Decimal:
    """Model estimate, not an exchange figure: max(20% of spot less OTM amount, 10% of spot) plus premium, per lot."""
    if kind == "CALL":
        otm = max(strike - spot, Decimal(0))
    elif kind == "PUT":
        otm = max(spot - strike, Decimal(0))
    else:
        raise ValueError("kind must be CALL or PUT")
    per_unit = max(Decimal("0.20") * spot - otm, Decimal("0.10") * spot) + premium
    return per_unit * Decimal(abs(quantity))


@dataclass(frozen=True)
class OptionPosition:
    kind: str
    strike: Decimal
    quantity: int  # signed; negative is short


def expiry_events(positions: Sequence[OptionPosition], spot: Decimal) -> list[str]:
    events: list[str] = []
    for p in positions:
        itm = spot > p.strike if p.kind == "CALL" else spot < p.strike
        if not itm:
            continue
        if p.quantity < 0:
            events.append(f"ASSIGNED {p.kind} {p.strike} x{abs(p.quantity)}")
        else:
            events.append(f"EXERCISED {p.kind} {p.strike} x{p.quantity}")
    return events


def pin_risk(strikes: Sequence[Decimal], short_quantities: Sequence[int], spot: Decimal, tolerance: Decimal) -> list[Decimal]:
    if len(strikes) != len(short_quantities):
        raise ValueError("strikes and quantities must align")
    return [k for k, q in zip(strikes, short_quantities, strict=True) if q < 0 and abs(spot - k) <= tolerance]
