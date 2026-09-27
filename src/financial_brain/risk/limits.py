"""The limits, and every breach carries the number that caused it.

``decisions/safety.py`` already answers the per-name half of "may we act": stale data, crisis
regime, liquidity against ADV, one position per promoter group, a drawdown pause. Those stay
where they are. This module answers the half that needs the **whole book**, and it does so by
comparing measured quantities to constants that are versioned with the code.

Nothing here is a threshold that was moved until a trade fitted. Each one has a reason stated
beside it, and changing one is a code change with that reason in the commit.

## The limits fall into four families

**Exposure** - how much of the book is in one thing: a name, a promoter group, the illiquid
tail. Counting problems, answerable from the position list.

**Risk** - how much of the book's *volatility* is in one thing, which is a different list. A 4%
position correlated 0.8 with everything else carries more risk than an 8% uncorrelated one, and
only the covariance shows that.

**Structure** - whether the book is as diversified as its position count suggests. Effective
bets from the eigenvalue spectrum, and the first factor's share of variance. A twelve-name book
at ENB 1.1 is one trade.

**Capacity** - whether the book can be got out of. Days to exit at a participation cap, and
the illiquid share. This is the family that is fine until it is catastrophic.

## Why a limit returns RESIZE and not only REFUSE

A limit that can only say no makes the caller guess. Where a breach is a *size* problem rather
than a *kind* problem - weight caps, risk-share caps, days-to-exit - the check returns the
largest weight that would pass, so the gate can propose a smaller position instead of nothing.
Where it is a kind problem - the book already holds this group, the regime forbids it - there is
no size that passes and the check says so.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# ------------------------------------------------------------------ exposure limits
#: One position's share of the book. Matches ``decisions/expected_value.MAX_WEIGHT``.
MAX_WEIGHT = 0.10
#: One promoter group's share. Above this, different listed entities are one bet.
MAX_GROUP_WEIGHT = 0.15
#: Share of the book in `small` + `micro` tiers, where the impact estimate is a bucket table
#: rather than a measurement and the round trip is 100-300 bps.
MAX_ILLIQUID_SHARE = 0.25
#: Share of the book whose promoter group could not be identified. Above this the
#: one-position-per-group rule in ``decisions/safety.py`` is largely inoperative, and a
#: concentration report on the rest is not a concentration report.
MAX_UNCLASSIFIED_SHARE = 0.40

# ---------------------------------------------------------------------- risk limits
#: Annualised volatility of the whole book. 25% is roughly a single large-cap Indian equity;
#: a diversified long-only book above that is levered by correlation rather than by borrowing.
MAX_PORTFOLIO_VOL_ANNUAL = 0.25
#: One position's share of total portfolio risk, from the Euler decomposition.
MAX_RISK_SHARE = 0.25
#: A candidate's correlation with anything already held. Above this it is a second helping.
MAX_CORRELATION_TO_HOLDING = 0.80
#: 95% expected shortfall over the horizon, as a fraction of the book. Empirical, not normal -
#: Indian equity returns are fat-tailed and a normal quantile understates the tail.
MAX_EXPECTED_SHORTFALL = 0.12

# ----------------------------------------------------------------- structure limits
#: Independent directions of risk (Meucci), from the eigenvalue spectrum. Below this the book
#: is concentrated whatever its position count.
MIN_EFFECTIVE_BETS = 2.5
#: The first principal component's share of the book's variance.
MAX_FIRST_FACTOR_SHARE = 0.60
#: A factor tilt, as the book's mean cross-sectional percentile. 0.5 is the universe; 0.80 is a
#: factor fund that nobody decided to run.
MAX_FACTOR_TILT = 0.80

# ------------------------------------------------------------------ capacity limits
#: Fraction of a name's average daily traded value the book may be.
MAX_PARTICIPATION = 0.10
#: Sessions to liquidate a position at that participation rate.
MAX_DAYS_TO_EXIT = 3.0


#: A breach is one of two kinds, and conflating them is how a gate becomes useless.
#:
#: ``limit`` - a measured quantity exceeded a threshold. The answer is no, or a smaller size.
#: ``data``  - the quantity could not be measured well enough to check. The answer is "I do not
#:             know", which is not the same as no.
#:
#: The distinction is not pedantic. The promoter-group coverage on the real book is 84%
#: unclassified, so a concentration limit treated as a ``limit`` breach refuses **every**
#: candidate forever - a gate that is correct and useless. As a ``data`` breach it produces
#: ABSTAIN and names the missing reference data, which is an actionable statement about the
#: archive rather than a verdict about a trade.
LIMIT, DATA = "limit", "data"


@dataclass
class Breach:
    """A limit that did not hold, with the measurement that failed it."""
    check: str
    limit: float
    measured: float
    why: str
    #: The largest weight that would have passed, where the breach is about size. ``None``
    #: when no size passes - a group already held is not a smaller position.
    max_passing_weight: float | None = None
    kind: str = LIMIT

    @property
    def resizable(self) -> bool:
        return self.max_passing_weight is not None and self.max_passing_weight > 0


@dataclass
class Verdict:
    breaches: list[Breach] = field(default_factory=list)
    measured: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.breaches

    @property
    def resize_to(self) -> float | None:
        """The largest weight passing **every** resizable breach, or None if any is fatal."""
        if not self.breaches:
            return None
        if self.data_gaps or any(not b.resizable for b in self.limit_breaches):
            return None
        return min(b.max_passing_weight for b in self.limit_breaches)

    @property
    def data_gaps(self) -> list[Breach]:
        return [b for b in self.breaches if b.kind == DATA]

    @property
    def limit_breaches(self) -> list[Breach]:
        return [b for b in self.breaches if b.kind == LIMIT]

    @property
    def verdict(self) -> str:
        """ABSTAIN outranks REFUSE: if something could not be measured, the honest answer is
        that it is unknown, not that it failed."""
        if self.data_gaps:
            return "ABSTAIN"
        if not self.limit_breaches:
            return "ALLOW"
        if any(not b.resizable for b in self.limit_breaches):
            return "REFUSE"
        return "RESIZE"


def check_book(exposure_report: dict, decomposition: dict | None = None) -> Verdict:
    """Limits that are about the book as it stands, with no candidate in view."""
    v = Verdict()
    e = exposure_report

    groups = e.get("by_group") or {}
    for name, share in groups.items():
        if name == "UNCLASSIFIED":
            continue
        if share > MAX_GROUP_WEIGHT:
            v.breaches.append(Breach(
                "exposure.group", MAX_GROUP_WEIGHT, share,
                f"promoter group {name!r} is {share:.1%} of the book"))
    unc = groups.get("UNCLASSIFIED", 0.0)
    if unc > MAX_UNCLASSIFIED_SHARE:
        v.breaches.append(Breach(
            "exposure.unclassified", MAX_UNCLASSIFIED_SHARE, unc,
            f"{unc:.1%} of the book has no identified promoter group, so the "
            f"one-position-per-group rule cannot bind on it. This is a gap in the promoter "
            f"reference data, not a measured concentration - fixing it means ingesting "
            f"shareholding-pattern coverage for these names.", kind=DATA))

    illiq = e.get("illiquid_share") or 0.0
    if illiq > MAX_ILLIQUID_SHARE:
        v.breaches.append(Breach(
            "capacity.illiquid", MAX_ILLIQUID_SHARE, illiq,
            f"{illiq:.1%} of the book is in small/micro tiers"))

    for f, tilt in (e.get("factor_tilts") or {}).items():
        if f.startswith("_") or f == "adv20":
            continue
        if tilt > MAX_FACTOR_TILT:
            v.breaches.append(Breach(
                "structure.factor_tilt", MAX_FACTOR_TILT, tilt,
                f"the book sits at the {tilt:.0%} percentile on {f} - a factor bet nobody "
                f"decided to take"))

    if decomposition:
        d = decomposition
        vol = d.get("sigma_annual")
        if vol is not None and vol > MAX_PORTFOLIO_VOL_ANNUAL:
            v.breaches.append(Breach(
                "risk.portfolio_vol", MAX_PORTFOLIO_VOL_ANNUAL, vol,
                f"annualised book volatility is {vol:.1%}"))
        enb = d.get("effective_bets")
        if enb is not None and enb == enb and enb < MIN_EFFECTIVE_BETS:
            v.breaches.append(Breach(
                "structure.effective_bets", MIN_EFFECTIVE_BETS, enb,
                f"{d.get('positions') and len(d['positions'])} positions but only "
                f"{enb:.1f} independent directions of risk"))
        ff = d.get("first_factor_share")
        if ff is not None and ff == ff and ff > MAX_FIRST_FACTOR_SHARE:
            v.breaches.append(Breach(
                "structure.first_factor", MAX_FIRST_FACTOR_SHARE, ff,
                f"one direction carries {ff:.0%} of the book's variance"))
        for p in d.get("positions", []):
            if p["risk_share"] > MAX_RISK_SHARE:
                v.breaches.append(Breach(
                    "risk.share", MAX_RISK_SHARE, p["risk_share"],
                    f"{p['name']} is {p['weight']:.1%} of the money and "
                    f"{p['risk_share']:.1%} of the risk "
                    f"(amplification {p['amplification']:.2f}x)",
                    # Risk share scales roughly linearly in weight near the current point,
                    # so the passing weight is the current one scaled by the ratio. Stated
                    # as an approximation because the relationship is not exactly linear.
                    max_passing_weight=p["weight"] * MAX_RISK_SHARE / p["risk_share"]))

    v.measured = {
        "positions": e.get("positions"),
        "gross_weight": e.get("gross_weight"),
        "largest_group": e.get("largest_group"),
        "unclassified_share": unc,
        "illiquid_share": illiq,
        "factor_tilts": e.get("factor_tilts"),
        "portfolio_vol_annual": (decomposition or {}).get("sigma_annual"),
        "effective_bets": (decomposition or {}).get("effective_bets"),
        "effective_positions": (decomposition or {}).get("effective_positions"),
        "first_factor_share": (decomposition or {}).get("first_factor_share"),
        "diversification_ratio": (decomposition or {}).get("diversification_ratio"),
    }
    return v


def check_candidate(*, weight: float, marginal: dict | None = None,
                    adv_inr: float | None = None, capital_inr: float | None = None,
                    group: str | None = None,
                    exposure_report: dict | None = None) -> Verdict:
    """Limits that are about adding one position to the book that exists.

    ``marginal`` is ``decompose.marginal_add`` output - the candidate priced *with* the book,
    not against it, which is the only way its correlation to what is already held is visible.
    """
    v = Verdict()

    if weight > MAX_WEIGHT:
        v.breaches.append(Breach(
            "exposure.weight", MAX_WEIGHT, weight,
            f"position would be {weight:.1%} of the book",
            max_passing_weight=MAX_WEIGHT))

    if group and exposure_report:
        held = (exposure_report.get("by_group") or {}).get(group, 0.0)
        after = held + weight
        if after > MAX_GROUP_WEIGHT:
            room = MAX_GROUP_WEIGHT - held
            v.breaches.append(Breach(
                "exposure.group", MAX_GROUP_WEIGHT, after,
                f"promoter group {group!r} would reach {after:.1%} "
                f"({held:.1%} already held)",
                max_passing_weight=room if room > 0 else None))

    if adv_inr and capital_inr:
        notional = weight * capital_inr
        participation = notional / adv_inr if adv_inr else float("inf")
        days = notional / (MAX_PARTICIPATION * adv_inr) if adv_inr else float("inf")
        if days > MAX_DAYS_TO_EXIT:
            cap_notional = MAX_DAYS_TO_EXIT * MAX_PARTICIPATION * adv_inr
            v.breaches.append(Breach(
                "capacity.days_to_exit", MAX_DAYS_TO_EXIT, days,
                f"exiting Rs {notional:,.0f} at {MAX_PARTICIPATION:.0%} of "
                f"Rs {adv_inr:,.0f} daily value takes {days:.1f} sessions",
                max_passing_weight=cap_notional / capital_inr))
        v.measured["participation"] = participation
        v.measured["days_to_exit"] = days

    if marginal:
        mc = marginal.get("max_correlation_to_book")
        if mc is not None and mc == mc and mc > MAX_CORRELATION_TO_HOLDING:
            v.breaches.append(Breach(
                "risk.correlation", MAX_CORRELATION_TO_HOLDING, mc,
                f"correlation {mc:.2f} with a name already held - this is a second "
                f"helping of an existing position, not a new one"))
        rs = marginal.get("risk_share")
        if rs is not None and rs > MAX_RISK_SHARE:
            v.breaches.append(Breach(
                "risk.share", MAX_RISK_SHARE, rs,
                f"would carry {rs:.1%} of the book's total risk at "
                f"{weight:.1%} of its money",
                max_passing_weight=weight * MAX_RISK_SHARE / rs))
        v.measured.update({
            "sigma_before": marginal.get("sigma_before"),
            "sigma_after": marginal.get("sigma_after"),
            "beta_to_book": marginal.get("beta_to_book"),
            "effective_bets_after": marginal.get("effective_bets_after"),
            "diversifies": marginal.get("diversifies"),
            "max_correlation_to_book": mc,
        })

    v.measured["weight"] = weight
    return v


def expected_shortfall(returns: list[float], *, level: float = 0.95) -> dict:
    """Empirical VaR and expected shortfall at ``level``, from realised returns.

    Empirical rather than normal: a normal quantile on Indian equity returns understates the
    tail, which is the only part of the distribution a risk limit is about. Reported with the
    tail's size so a caller can see whether it is estimated from six observations.
    """
    if len(returns) < 20:
        return {"var": float("nan"), "es": float("nan"), "n": len(returns),
                "tail_n": 0, "note": "fewer than 20 observations; no tail to estimate"}
    xs = sorted(returns)
    k = max(1, int(math.floor((1 - level) * len(xs))))
    tail = xs[:k]
    return {"var": xs[k - 1], "es": sum(tail) / len(tail), "n": len(xs),
            "tail_n": k, "level": level,
            "breaches_limit": abs(sum(tail) / len(tail)) > MAX_EXPECTED_SHORTFALL}
