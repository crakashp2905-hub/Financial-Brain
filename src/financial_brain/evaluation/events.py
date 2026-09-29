"""Is the event archive worth anything that a momentum sort does not already give you?

[[Naive momentum beats it, and the horizon was a fit]] left this project with one honest question:
`ORDER BY mom_12_1 DESC` beat every signal built on top of eleven years of point-in-time data, so what
does any of the rest of it do? This module answers that for the one asset that is genuinely
unusual - 3.08M announcements reduced to 326,179 typed event rows keyed on lineage, 2015 to 2026.

## The test is matched, not raw

An unmatched event study rediscovers that events happen to stocks that were already moving. Order wins
are announced by companies doing well; insolvency filings by companies that have already fallen. Regress
forward returns on an event flag with no control and the coefficient is momentum wearing a costume.

So every event is compared against a **control drawn from the same session and the same momentum
decile** that did not have the event. The difference between those two means is the part of the event
that momentum does not already explain, which is exactly the quantity the baseline comparison demanded.

## Three statistics that decide the answer

**Clustering by date.** Events arrive in clusters - earnings season, budget day, a sector-wide
regulatory action. Treating 11,931 order wins as 11,931 independent observations inflates the
t-statistic by roughly the square root of the events per date. The effect is therefore measured as a
per-session series and the t-statistic is computed on *that*, over sessions, exactly as the IC
t-statistic is in ``forecasting/walkforward``.

**Excess, not raw.** The forward return is measured against the point-in-time eligible universe's mean
over the same window. A raw forward return of +4% during a month the market rose 6% is a loss.

**The multiple-testing bar.** Eleven event types at two horizons is twenty-two more trials charged
against a ledger that already holds 151. :func:`census` reports each effect against the Bonferroni bar
from :mod:`.families` rather than against zero, because against zero roughly one in twenty of these
will look significant by construction.

## What this cannot see

The horizon counts **rows**, not sessions: ``LEAD(close_adj, h)`` is h trading rows forward for that
lineage, which is h sessions only if the name traded every session in between. A name that was
suspended for a week gets a longer window than its peers. That biases toward names that trade
continuously, which is the same direction as the liquidity filter already applied, and it is stated
rather than corrected because correcting it means dropping exactly the names an insolvency event
selects for.
"""
from __future__ import annotations

import math
from datetime import date

from . import families

#: Event columns in ``event_flags``. ``e_buyback`` exists in the schema with zero rows and is excluded
#: rather than reported as an effect of nothing.
EVENTS = (
    "e_management_change", "e_clarification", "e_promoter_pledge", "e_credit_rating",
    "e_order_win", "e_acquisition", "e_insolvency", "e_scheme", "e_fund_raising",
    "e_auditor_resignation", "e_joint_venture",
)

#: Horizons in trading rows.
HORIZONS = (20, 60)

#: Quantiles per matching dimension. Ten on one dimension, five on two: matching on two features at
#: ten deciles each would need a hundred cells per session and most would be empty, so the cells are
#: coarser when there are more of them.
DECILES = 10
DECILES_2D = 5

#: What an event is matched against. ``mom_12_1`` alone differences out twelve-month momentum, which
#: is what the baseline comparison demanded - but it leaves the *recent* move in. That matters for
#: exactly the events with the strongest effects: an exchange demands a clarification BECAUSE a price
#: moved, so a clarification flag is partly a marker of a move that has already happened, and an
#: effect measured without controlling for it is short-horizon continuation wearing a costume. Adding
#: ``ret_20d`` differences that out too, and an effect that survives both is an effect of the event.
MATCH_1D = ("mom_12_1",)
MATCH_2D = ("mom_12_1", "ret_20d")

#: Sessions with a usable matched pair needed before an effect is reported. Below this the t-statistic
#: is over a handful of numbers.
MIN_SESSIONS = 30

#: Minimum event cases on a session for that session to contribute. One event matched against a decile
#: is a single draw, not a mean.
MIN_CASES_PER_SESSION = 1


class EventError(ValueError):
    pass


def _effect_sql(event: str, match=MATCH_1D) -> str:
    """The matched event study, as one query returning a per-session effect series.

    Written out rather than assembled because the joins are the experiment: a mistake in the matching
    condition produces a number that looks exactly like an answer.
    """
    if event not in EVENTS:
        raise EventError(f"unknown event {event!r}; have {list(EVENTS)}")
    if not match:
        raise EventError("an unmatched event study measures momentum, not the event")
    q = DECILES if len(match) == 1 else DECILES_2D
    buckets = ",\n                   ".join(
        f"NTILE({q}) OVER (PARTITION BY f.business_date ORDER BY f.{c}, f.lineage) AS b_{c}"
        for c in match)
    not_null = " AND ".join(f"f.{c} IS NOT NULL" for c in match)
    cell = ", ".join(f"b_{c}" for c in match)
    return f"""
        WITH elig AS (
            -- Point-in-time eligible, bucketed on each matching dimension. The buckets are computed
            -- within the session, so they are cross-sectional ranks and not levels.
            SELECT f.business_date, f.lineage,
                   {buckets}
            FROM features f
            WHERE f.adv20 >= ? AND {not_null}
              AND f.business_date BETWEEN ? AND ?
        ), fwd AS (
            SELECT lineage, business_date,
                   LEAD(close_adj, ?) OVER (PARTITION BY lineage ORDER BY business_date)
                     / close_adj - 1 AS ret
            FROM adjusted_prices
            WHERE close_adj > 0
        ), joined AS (
            SELECT e.business_date, e.lineage, {cell}, f.ret,
                   COALESCE(v.{event}, FALSE) AS hit
            FROM elig e
            JOIN fwd f USING (lineage, business_date)
            LEFT JOIN event_flags v USING (lineage, business_date)
            WHERE f.ret IS NOT NULL
        ), universe AS (
            -- The bar every return is measured against: the eligible universe's mean over the same
            -- window, per session.
            SELECT business_date, AVG(ret) AS uni FROM joined GROUP BY business_date
        ), cells AS (
            -- One row per (session, cell): the event mean and the control mean inside that cell.
            SELECT j.business_date, {cell},
                   AVG(CASE WHEN j.hit THEN j.ret END) AS event_ret,
                   AVG(CASE WHEN NOT j.hit THEN j.ret END) AS control_ret,
                   SUM(CASE WHEN j.hit THEN 1 ELSE 0 END) AS n_event,
                   SUM(CASE WHEN NOT j.hit THEN 1 ELSE 0 END) AS n_control
            FROM joined j
            GROUP BY j.business_date, {cell}
        )
        SELECT c.business_date,
               -- Average the within-cell differences, so the matching features are differenced out
               -- cell by cell rather than adjusted for afterwards.
               AVG(c.event_ret - c.control_ret) AS effect,
               SUM(c.n_event) AS cases,
               AVG(c.event_ret) - MAX(u.uni) AS event_excess,
               AVG(c.control_ret) - MAX(u.uni) AS control_excess
        FROM cells c JOIN universe u USING (business_date)
        WHERE c.event_ret IS NOT NULL AND c.control_ret IS NOT NULL
          AND c.n_event >= ? AND c.n_control >= 5
        GROUP BY c.business_date
        ORDER BY c.business_date
    """


def event_study(con, *, event: str, horizon: int = 20, start: date | None = None,
                end: date | None = None, min_adv: float = 1e7,
                min_cases: int = MIN_CASES_PER_SESSION, match=MATCH_1D) -> dict:
    """The momentum-matched effect of one event at one horizon.

    Returns the per-session effect series, its mean, and a t-statistic computed **over sessions**. The
    series is kept so :mod:`.families` can estimate how many of these trials are independent.
    """
    start = start or date(2015, 1, 1)
    end = end or date(2026, 12, 31)
    rows = con.execute(_effect_sql(event, match),
                       [min_adv, start, end, horizon, min_cases]).fetchall()
    if len(rows) < MIN_SESSIONS:
        return {"event": event, "horizon": horizon, "sessions": len(rows),
                "matched_on": list(match), "mean_effect": None, "t": None,
                "why": f"{len(rows)} sessions with a matched pair is below {MIN_SESSIONS}"}

    effects = [r[1] for r in rows]
    dates = [r[0] for r in rows]
    n = len(effects)
    m = sum(effects) / n
    var = sum((x - m) ** 2 for x in effects) / (n - 1)
    sd = math.sqrt(var)
    cases = sum(r[2] for r in rows)
    return {
        "event": event,
        "horizon": horizon,
        "matched_on": list(match),
        "sessions": n,
        "cases": cases,
        "cases_per_session": cases / n,
        "mean_effect": m,
        "sd": sd,
        "t": (m / (sd / math.sqrt(n))) if sd > 0 else None,
        "mean_event_excess": sum(r[3] for r in rows) / n,
        "mean_control_excess": sum(r[4] for r in rows) / n,
        "effects": effects,
        "dates": dates,
        "why": "the effect is the within-decile difference between names with the event and names "
               "without it on the same session, so momentum is differenced out; the t is over "
               "sessions because events cluster in time",
    }


def census(con, *, events=EVENTS, horizons=HORIZONS, start: date | None = None,
           end: date | None = None, min_adv: float = 1e7, match=MATCH_1D,
           progress=None) -> dict:
    """Every event at every horizon, against the multiple-testing bar rather than against zero.

    The bar comes from the shared ledger, so it already carries the 151 trials spent before this
    module existed. Running this census is itself ``len(events) * len(horizons)`` more trials, and the
    returned ``bar_after`` is what everything in the project must clear once they are recorded.
    """
    before = families.census(con)
    results = []
    for e in events:
        for h in horizons:
            r = event_study(con, event=e, horizon=h, start=start, end=end,
                            min_adv=min_adv, match=match)
            results.append(r)
            if progress:
                progress(r)

    trials_added = len(events) * len(horizons)
    bar_after = families.bonferroni(before["trials"] + trials_added)
    usable = [r for r in results if r.get("t") is not None]
    clears = [r for r in usable if abs(r["t"]) >= bar_after]
    nominal = [r for r in usable if abs(r["t"]) >= 1.96]
    return {
        "results": sorted(usable, key=lambda r: -abs(r["t"])),
        "unusable": [r for r in results if r.get("t") is None],
        "matched_on": list(match),
        "trials_before": before["trials"],
        "trials_added": trials_added,
        "bar_before": before["bar_by_trial"],
        "bar_after": bar_after,
        "clears_the_bar": [(r["event"], r["horizon"], r["t"]) for r in clears],
        "significant_at_nominal_five_percent": len(nominal),
        "expected_by_chance_at_nominal": 0.05 * len(usable),
        "why": "an effect is judged against the Bonferroni bar over every trial in the ledger, not "
               "against 1.96; at nominal significance one in twenty of these is expected to pass by "
               "construction, which is why the count and the expectation are both reported",
    }
