"""Expected net alpha: the one number every candidate is compared on, itemised so nothing is hidden in it.

    expected return        the name's expected total return over the horizon
  - benchmark              what the liquid universe is expected to earn over the same horizon
  - fees                   statutory charges and brokerage on both legs (the cost schedule, incl. DP charge)
  - spread                 crossing the bid-ask on both legs, by liquidity bucket
  - impact                 square-root market impact of the order on both legs
  = expected net alpha

  - risk penalty           the variance this position adds at its planned weight, priced at RISK_AVERSION
  = risk-adjusted utility (not an economic return or alpha)

Everything is a fraction of the position over the horizon. The expected *excess* return comes from a measured
model (for the scanners: what the name's composite decile earned in the 2016-26 replay, scaled to the horizon), so
expected return = benchmark + excess; the benchmark's own level is an assumption (MARKET_DRIFT), stated as one, and
it cancels out of the net. The spread and impact are separate because a strategy can be right about the stock and
still lose to execution, and the paper ledger measures the two separately afterwards.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from ..costs.india import CostModel, Segment

MARKET_DRIFT = 0.12            #: assumed annual return of the liquid universe; cancels out of the net
RISK_AVERSION = 3.0            #: mean-variance risk aversion for the penalty (assumption, stated)
#: One-way half-spread by liquidity bucket, bp - the intraday engine's replay assumption; the live paper ledger
#: measured about 4 bp a side on the 200 most liquid names.
HALF_SPREAD_BP = {"mega": 2.0, "large": 4.0, "mid": 8.0, "small": 20.0}


def bucket(adv_inr: float) -> str:
    """Liquidity bucket by 20-day average turnover (Rs)."""
    return "mega" if adv_inr >= 5e9 else "large" if adv_inr >= 1e9 else "mid" if adv_inr >= 2e8 else "small"


@dataclass(frozen=True)
class NetAlpha:
    horizon: int
    expected_return: float
    benchmark: float
    fees: float
    spread: float
    impact: float
    risk_penalty: float
    source: str = ""

    @property
    def excess(self) -> float:
        return self.expected_return - self.benchmark

    @property
    def costs(self) -> float:
        return self.fees + self.spread + self.impact

    @property
    def net(self) -> float:
        return self.excess - self.costs

    @property
    def utility(self) -> float:
        return self.net - self.risk_penalty

    def as_dict(self) -> dict:
        return {**asdict(self), "excess": self.excess, "costs": self.costs,
                "net": self.net, "utility": self.utility}

    def lines(self) -> list[tuple[str, float]]:
        return [("Expected return", self.expected_return), ("Expected benchmark", -self.benchmark),
                ("Fees and taxes", -self.fees), ("Spread", -self.spread), ("Market impact", -self.impact),
                ("Expected net alpha", self.net), ("Risk penalty", -self.risk_penalty),
                ("Risk-adjusted utility", self.utility)]

    def render(self) -> str:
        rows = self.lines()
        out = [f"{k:20s} {v * 100:+7.2f}%" for k, v in rows[:-1]]
        out.append("-" * 29)
        out.append(f"{rows[-1][0]:20s} {rows[-1][1] * 100:+7.2f}%")
        return "\n".join(out)


def compute(*, excess: float, horizon: int, order_value: float, adv_inr: float, vol_annual: float,
            weight: float, segment: Segment = Segment.DELIVERY, costs: CostModel | None = None,
            source: str = "") -> NetAlpha:
    """Itemise a candidate. ``excess``: the expected return over the benchmark at ``horizon`` sessions."""
    costs = costs or CostModel()
    if order_value <= 0 or adv_inr <= 0 or vol_annual <= 0:
        raise ValueError("order value, turnover and volatility must be positive")
    bench = (1 + MARKET_DRIFT) ** (horizon / 252.0) - 1
    fees = costs.round_trip(turnover=order_value, segment=segment, include_impact=False)["total"] / order_value
    spread = 2 * HALF_SPREAD_BP[bucket(adv_inr)] / 1e4
    impact = 2 * costs.sqrt_impact(order_value=order_value, adv_value=adv_inr, daily_vol=vol_annual / np.sqrt(252.0))
    sigma_h2 = vol_annual ** 2 * horizon / 252.0
    risk = RISK_AVERSION * max(weight, 0.0) * sigma_h2
    return NetAlpha(horizon=horizon, expected_return=bench + excess, benchmark=bench, fees=float(fees),
                    spread=float(spread), impact=float(impact), risk_penalty=float(risk), source=source)
