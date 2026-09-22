"""Expected value and risk-based sizing: the arithmetic a decision must survive.

"The model is 82% confident" is not a reason to buy anything. It says nothing about how
much is made when right, how much is lost when wrong, or how much to put on. This module
replaces confidence with the three numbers that actually decide a trade:

    expected value      sum of probability x return across named scenarios
    downside            the probability-weighted loss, and the worst case
    size                risk budget divided by the distance to invalidation

The sizing rule is the important one, and it inverts the usual order. Most systems go
*confidence -> size*, which sizes up exactly when the model is most sure and therefore
most wrong in the tail. This goes **thesis -> invalidation -> size**: the position is
whatever loses no more than the risk budget if the invalidation level is reached. A thesis
with a distant invalidation gets a small position; a tight one can carry more.

Nothing here predicts anything. It is arithmetic over numbers the thesis already had to
state, and its purpose is to make a bad trade fail the sum rather than fail in the market.
"""
from __future__ import annotations

from dataclasses import dataclass

TOLERANCE = 0.01            # probabilities must sum to 1 within this
MIN_SCENARIOS = 2           # a single scenario is a forecast, not a distribution
MAX_WEIGHT = 0.10           # no position is more than this share of the portfolio
DEFAULT_RISK_BUDGET = 0.005  # 0.5% of the portfolio at risk per position


class EVError(ValueError):
    """The arithmetic a decision must satisfy before it can be sized."""


@dataclass
class Scenario:
    name: str
    probability: float
    ret: float              # fractional return over the horizon


@dataclass
class Assessment:
    scenarios: list[Scenario]
    expected_value: float
    upside: float           # probability-weighted return of the positive scenarios
    downside: float         # probability-weighted return of the negative ones
    worst: float
    reward_to_risk: float | None

    def positive(self) -> bool:
        return self.expected_value > 0

    def describe(self) -> str:
        return (f"EV {self.expected_value:+.1%} (upside {self.upside:+.1%}, downside "
                f"{self.downside:+.1%}, worst {self.worst:+.1%})")


def parse(scenarios: dict | list) -> list[Scenario]:
    """Accept {"bull": {"probability": .35, "return": .25}, ...} or a list of dicts."""
    items = (scenarios.items() if isinstance(scenarios, dict)
             else [(s.get("name", f"s{i}"), s) for i, s in enumerate(scenarios)])
    out = []
    for name, body in items:
        if not isinstance(body, dict):
            raise EVError(f"scenario {name!r} must be a mapping with probability and return")
        try:
            p = float(body["probability"])
            r = float(body.get("return", body.get("ret")))
        except (KeyError, TypeError, ValueError) as e:
            raise EVError(f"scenario {name!r} needs a probability and a return") from e
        out.append(Scenario(name=str(name), probability=p, ret=r))
    return out


def assess(scenarios: dict | list) -> Assessment:
    """The distribution a thesis implies, or an error saying why it is not one."""
    items = parse(scenarios)
    if len(items) < MIN_SCENARIOS:
        raise EVError(f"needs at least {MIN_SCENARIOS} scenarios; one scenario is a "
                      f"forecast, not a distribution")
    if any(s.probability < 0 or s.probability > 1 for s in items):
        raise EVError("every probability must be between 0 and 1")
    total = sum(s.probability for s in items)
    if abs(total - 1.0) > TOLERANCE:
        raise EVError(f"probabilities sum to {total:.2f}, not 1")
    if not any(s.ret < 0 for s in items):
        raise EVError("no scenario loses money - name the way this is wrong")

    ev = sum(s.probability * s.ret for s in items)
    upside = sum(s.probability * s.ret for s in items if s.ret > 0)
    downside = sum(s.probability * s.ret for s in items if s.ret < 0)
    worst = min(s.ret for s in items)
    rr = (upside / abs(downside)) if downside else None
    return Assessment(scenarios=items, expected_value=ev, upside=upside,
                      downside=downside, worst=worst, reward_to_risk=rr)


def size(*, portfolio_inr: float, entry: float, invalidation: float,
         risk_budget: float = DEFAULT_RISK_BUDGET,
         max_weight: float = MAX_WEIGHT) -> dict:
    """How much to buy, from the distance to being proved wrong.

    thesis -> invalidation -> size, never confidence -> size. The position is the one
    that loses exactly ``risk_budget`` of the portfolio if the invalidation level trades.
    """
    if entry <= 0:
        raise EVError("entry price must be positive")
    if invalidation <= 0 or invalidation >= entry:
        raise EVError(f"invalidation {invalidation} must be below the entry {entry} for a "
                      f"long - otherwise there is no distance to be wrong over")
    if not 0 < risk_budget <= 0.05:
        raise EVError("risk budget must be a positive fraction no greater than 5%")

    risk_per_share = entry - invalidation
    rupees_at_risk = portfolio_inr * risk_budget
    shares = int(rupees_at_risk // risk_per_share)
    notional = shares * entry
    weight = notional / portfolio_inr if portfolio_inr else 0.0

    capped = False
    if weight > max_weight:
        capped = True
        notional = portfolio_inr * max_weight
        shares = int(notional // entry)
        notional = shares * entry
        weight = notional / portfolio_inr

    return {"shares": shares, "notional_inr": round(notional, 2),
            "weight": round(weight, 4), "risk_per_share": round(risk_per_share, 4),
            "rupees_at_risk": round(min(shares * risk_per_share, rupees_at_risk), 2),
            "stop_distance": round(risk_per_share / entry, 4),
            "capped_by_max_weight": capped}


def review(content: dict, *, portfolio_inr: float = 1_000_000.0) -> dict:
    """The full arithmetic for one decision's content, as the risk review sees it."""
    assessment = assess(content.get("scenarios") or {})
    sizing = content.get("sizing") or {}
    entry, invalidation = sizing.get("entry"), sizing.get("invalidation")
    out = {"expected_value": assessment.expected_value,
           "upside": assessment.upside, "downside": assessment.downside,
           "worst": assessment.worst, "reward_to_risk": assessment.reward_to_risk,
           "describe": assessment.describe()}
    if entry and invalidation:
        out["size"] = size(portfolio_inr=portfolio_inr, entry=float(entry),
                           invalidation=float(invalidation),
                           risk_budget=float(sizing.get("risk_budget",
                                                        DEFAULT_RISK_BUDGET)))
    return out
