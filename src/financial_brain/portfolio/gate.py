"""The Portfolio Gate: can the book afford this, which is not whether it is any good.

Two questions get confused constantly, and keeping them apart is the whole design:

    Is this opportunity attractive?     decisions/expected_value.py - scenarios, EV, sizing
    May we act at all?                  decisions/safety.py - staleness, regime, group, drawdown
    Can the portfolio afford it?        here

The third is not a weaker form of the first. A trade with excellent expected value must still be
refused when the book already holds the same bet, cannot be got out of at that size, or has no
risk budget left. And critically, the gate's usual answer is not yes or no but **a smaller
number**: most breaches are about size, and a gate that can only refuse makes the caller guess.

## The order matters, and it is cheapest-first

1. **Safety** (existing, per-name): stale data, crisis regime, liquidity, group, drawdown. Free
   to evaluate and fatal when breached, so it runs before anything is estimated.
2. **Book limits**: what the existing book already breaches. A book over its volatility limit
   cannot accept *any* new position, and finding that out before estimating a covariance with the
   candidate in it saves the estimation.
3. **Covariance**, once, over held names plus the candidate. The candidate is priced **with** the
   book, never against it - its correlation to what is already held is the number that decides
   most refusals and it does not exist in a separate estimate.
4. **Marginal analysis**: what adding it does to portfolio volatility, risk shares, effective
   bets.
5. **Candidate limits**, which can return a smaller passing weight.
6. **Stress**, last, because it is the most expensive and only matters for a position that has
   survived everything else.

## Verdicts

``ALLOW``   every limit holds at the requested weight.
``RESIZE``  every breach is about size, and there is a weight that passes all of them.
``REFUSE``  at least one breach has no passing size - the book holds this bet already, or the
            name cannot be exited, or the book itself is over a limit.
``ABSTAIN`` the gate could not decide, because something it needs is missing or not estimable.
            Distinct from REFUSE on purpose: "no" and "I do not know" are different answers and
            collapsing them hides data gaps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..decisions import safety
from ..risk import covariance, decompose, exposure, limits, stress

ALLOW, RESIZE, REFUSE, ABSTAIN = "ALLOW", "RESIZE", "REFUSE", "ABSTAIN"

#: Sessions of history the covariance is estimated over. One year: long enough that 30 names are
#: not under-determined, short enough that the correlations are the current ones.
COV_SESSIONS = 250


@dataclass
class Decision:
    verdict: str
    requested_weight: float
    approved_weight: float
    reasons: list[str] = field(default_factory=list)
    breaches: list[dict] = field(default_factory=list)
    measured: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "requested_weight": self.requested_weight,
                "approved_weight": self.approved_weight, "reasons": self.reasons,
                "breaches": self.breaches, "measured": self.measured}


def _breach_dicts(v: limits.Verdict) -> list[dict]:
    return [{"check": b.check, "limit": b.limit, "measured": b.measured, "why": b.why,
             "max_passing_weight": b.max_passing_weight, "resizable": b.resizable,
             "kind": b.kind}
            for b in v.breaches]


def evaluate(con, *, isin: str, weight: float, as_of: date,
             capital_inr: float, adv_inr: float | None = None,
             window_days: int | None = 400, with_stress: bool = True,
             cov_sessions: int = COV_SESSIONS) -> Decision:
    """Run the gate for one candidate against the book as it stands on ``as_of``.

    ``window_days`` chooses the book: ``None`` is the open book, a number is everything entered
    in that many days. With no positions currently open, the windowed form is what has anything
    in it, and running the gate against the book the system *did* hold is the only way to see
    what it would have refused.
    """
    d = Decision(verdict=ABSTAIN, requested_weight=weight, approved_weight=0.0)

    # ---------------------------------------------------------------- 1. safety, per name
    notional = weight * capital_inr
    sa = safety.assess(con, isin=isin, as_of=as_of, position_inr=notional)
    # `safe` respects each breach's `blocking` flag; a non-blocking note is not a refusal.
    if not sa.safe:
        d.verdict = REFUSE
        d.reasons += [f"safety.{b.check}: {b.detail}" for b in sa.breaches]
        d.breaches += [{"check": f"safety.{b.check}", "why": b.detail,
                        "resizable": False, "max_passing_weight": None}
                       for b in sa.breaches]
        d.measured["safety_breaches"] = len(sa.breaches)
        return d

    # ------------------------------------------------------------------- 2. the book alone
    book = exposure.report(con, as_of, window_days=window_days)
    held = {}
    for p in exposure.positions(con, as_of, window_days=window_days):
        held[p["isin"]] = held.get(p["isin"], 0.0) + (p["weight"] or 0.0)
    d.measured["book"] = {k: book[k] for k in
                          ("positions", "gross_weight", "illiquid_share",
                           "unclassified_group_share", "industry_available")}

    # ------------------------------------------------- 3. covariance, candidate with book
    names = sorted(set(held) | {isin})
    decomp = marg = None
    if len(names) >= 3:
        try:
            r = covariance.returns(con, names, end=as_of, sessions=cov_sessions)
            # Pairwise-complete, because strict intersection let one young name truncate the
            # whole book: 26 names over a 250-session request collapsed to 105 sessions, four
            # observations per asset, and a shrinkage intensity of 0.998 - at which point the
            # risk numbers describe the shrinkage target and not the market.
            est = covariance.shrink_pairwise(r["sparse"], r["isins"])
            order = est["order"]
            w_book = [held.get(k, 0.0) for k in order]
            if any(w_book):
                decomp = decompose.contributions(est["cov"], w_book, order)
            if isin in order:
                marg = decompose.marginal_add(est["cov"], w_book, order,
                                              candidate=isin, weight=weight)
            d.measured["covariance"] = {
                "assets": est["assets"], "obs": est["obs"],
                "obs_per_asset": est["obs_per_asset"],
                "shrinkage_delta": est["delta"],
                "shrinkage_method": est["method"],
                "min_pair_obs": est.get("min_pair_obs"),
                "intersection_sessions": r["intersection_sessions"],
                "union_sessions": r["union_sessions"],
                "avg_correlation": est["avg_correlation"],
                "under_determined": est["underdetermined"],
                "sample_was_pd": est["sample_is_pd"],
                "ridge_added": est["ridge_added"],
                "dropped_for_short_history": r["dropped"],
            }
        except (covariance.CovarianceError, ValueError) as exc:
            # ABSTAIN rather than ALLOW: a position sized without knowing its correlation to
            # the book is sized on an assumption nobody stated.
            d.reasons.append(f"covariance not estimable: {exc}")
            d.measured["covariance_error"] = str(exc)
            return d
    else:
        d.reasons.append(
            f"only {len(names)} names including the candidate; correlation structure is not "
            f"meaningful and the gate will not pretend to have checked it")
        return d

    if decomp:
        d.measured["risk"] = {
            "portfolio_vol_annual": decomp["sigma_annual"],
            "effective_bets": decomp["effective_bets"],
            "effective_positions": decomp["effective_positions"],
            "first_factor_share": decomp["first_factor_share"],
            "diversification_ratio": decomp["diversification_ratio"],
        }

    # A covariance estimated at fewer than UNDERDETERMINED observations per asset is mostly the
    # shrinkage target, so the structure limits - effective bets, first factor share, portfolio
    # volatility - would be checked against a number the data did not supply. The gate reports
    # them and refuses to *gate* on them, which is the difference between ABSTAIN and REFUSE.
    trust_structure = not (d.measured.get("covariance", {}).get("under_determined", True))
    book_v = limits.check_book(book, decomp if trust_structure else None)
    if not trust_structure:
        d.reasons.append(
            f"structure limits not applied: covariance has "
            f"{d.measured['covariance']['obs_per_asset']:.1f} observations per asset, below "
            f"{covariance.UNDERDETERMINED:.0f}, so effective bets and portfolio volatility "
            f"are properties of the shrinkage target rather than measurements")
        d.measured["structure_limits_applied"] = False
    if not book_v.ok:
        # The book's own breaches are not the candidate's fault and no size fixes them. A data
        # gap yields ABSTAIN rather than REFUSE - "concentration is unverifiable" is a statement
        # about the archive, and answering it with "no" forever would make the gate useless
        # while looking rigorous.
        d.verdict = ABSTAIN if book_v.data_gaps else REFUSE
        d.reasons += [f"book.{b.check}: {b.why}" for b in book_v.breaches]
        d.breaches += _breach_dicts(book_v)
        d.measured["book_limits"] = book_v.measured
        d.measured["book_data_gaps"] = [b.check for b in book_v.data_gaps]
        return d
    d.measured["book_limits"] = book_v.measured

    # ------------------------------------------------------------- 5. the candidate itself
    group = None
    grow = con.execute("SELECT MIN(anchor) FROM promoter_groups WHERE member = ?",
                       [isin]).fetchone()
    if grow and grow[0]:
        group = grow[0]
    cand_v = limits.check_candidate(weight=weight, marginal=marg, adv_inr=adv_inr,
                                    capital_inr=capital_inr, group=group,
                                    exposure_report=book)
    d.measured["candidate"] = cand_v.measured
    d.measured["candidate"]["promoter_group"] = group
    d.breaches += _breach_dicts(cand_v)

    if cand_v.data_gaps:
        d.verdict, d.approved_weight = ABSTAIN, 0.0
    elif cand_v.ok:
        d.verdict, d.approved_weight = ALLOW, weight
    elif cand_v.resize_to:
        d.verdict, d.approved_weight = RESIZE, cand_v.resize_to
        d.reasons.append(f"resized from {weight:.2%} to {cand_v.resize_to:.2%}")
    else:
        d.verdict = REFUSE
    d.reasons += [f"candidate.{b.check}: {b.why}" for b in cand_v.breaches]

    # ----------------------------------------------------------------------- 6. stress
    if with_stress and d.verdict in (ALLOW, RESIZE):
        after = dict(held)
        after[isin] = after.get(isin, 0.0) + d.approved_weight
        tot = sum(after.values()) or 1.0
        st = stress.report(con, {k: v / tot for k, v in after.items()},
                           end=as_of, capital_inr=capital_inr)
        w21 = (st.get("windows") or {}).get(21, {})
        es = w21.get("expected_shortfall")
        d.measured["stress"] = {
            "sessions": st.get("sessions"),
            "es95_21d": es,
            "worst_21d": (w21.get("worst") or [{}])[0].get("return"),
            "worst_21d_dates": [(w21.get("worst") or [{}])[0].get("start"),
                                (w21.get("worst") or [{}])[0].get("end")],
            "vix_vol_multiplier": (st.get("conditional_vol") or {}).get(
                "vix_vol_multiplier"),
            "crisis_turnover_ratio": (st.get("conditional_liquidity") or {}).get(
                "turnover_ratio_median"),
        }
        if es is not None and abs(es) > limits.MAX_EXPECTED_SHORTFALL:
            d.verdict = REFUSE
            d.approved_weight = 0.0
            d.reasons.append(
                f"stress.expected_shortfall: 21-session ES95 of {es:.1%} exceeds "
                f"{limits.MAX_EXPECTED_SHORTFALL:.0%}")
            d.breaches.append({"check": "stress.expected_shortfall",
                               "limit": limits.MAX_EXPECTED_SHORTFALL, "measured": es,
                               "why": "the book's own worst months, replayed",
                               "resizable": False, "max_passing_weight": None})
    return d
