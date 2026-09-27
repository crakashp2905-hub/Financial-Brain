"""Find which names satisfy which measured condition today, and attach each one's record.

The scan is point-in-time by construction: it reads the most recent ``features`` row at or before
``as_of`` and nothing after it, and the cross-sectional ranks are computed within that session
rather than against a fixed cutoff that drifts with the market.

## The prior is looked up, never assumed

Every emitted opportunity carries the condition's record from ``evaluation_runs``: the best
t-statistic it has achieved, how many trials it has cost, the Bonferroni bar that applied, and the
verdict. The bar is recomputed from the *current* ledger size rather than stored, because it rises
with every trial and a bar quoted from when the test was run is out of date the moment anything
else is tested.

A condition with no record is **not emitted**. It cannot happen given ``conditions.py`` only
admits measured conditions, and the check is here anyway because a silent fallback to "no prior
known" is how an unmeasured signal would eventually reach a human looking like a measured one.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import date
from statistics import NormalDist

from . import conditions

N = NormalDist()
ALPHA = 0.05

#: Verdicts recorded before this date were reached under the **flat** cost model - one impact
#: bucket charged to a whole book - which was corrected on 2026-09-27 to charge each holding its
#: own liquidity tier. A PROMOTE from before it is not a current pass and is flagged rather than
#: repeated: the ledger holds exactly one, `mom_12_1` at h=20 with params `{"bucket": "mid"}`,
#: and re-measured with per-name costs that signal gives a net t of +2.63 against a bar of 3.59.
#:
#: The same applies to the quintile tie-break fix of the same date, which changed measured
#: turnover for every boolean feature.
COST_MODEL_EPOCH = date(2026, 9, 27)

#: Names satisfying a condition, above which the condition is reporting a market state rather
#: than an opportunity. A "signal" firing on 40% of the universe is a description of the weather.
MAX_UNIVERSE_SHARE = 0.35
#: Minimum 20-session traded value for a name to be screened at all.
MIN_ADV_INR = 1e7


@dataclass
class Opportunity:
    opportunity_id: str
    isin: str
    ticker: str | None
    condition: str
    detected_on: date
    direction: int
    claim: str
    source: str
    #: The condition's measured record, from the trial ledger. Never optional.
    prior: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    catalysts: list[dict] = field(default_factory=list)
    caveat: str | None = None
    status: str = "DISCOVERED"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["detected_on"] = str(self.detected_on)
        return d


def bonferroni_bar(con, *, extra: int = 1, alpha: float = ALPHA) -> dict:
    """The |t| a new result must clear, from the ledger as it stands now."""
    n = con.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0] + extra
    return {"trials": n, "bar": N.inv_cdf(1 - alpha / (2 * n)), "alpha": alpha}


def prior_for(con, cond: conditions.Condition, bar: dict) -> dict:
    """What the ledger says this condition has been worth. The **verdict** is authoritative.

    The first version of this compared the ledger's best ``ic_t`` to the Bonferroni bar and
    reported CLEARS when it won. That is wrong in the most consequential way available, and it
    made the engine do the opposite of its job.

    ``ic_t`` is the t-statistic of the **information coefficient** - whether the signal orders
    names correctly. It says nothing about whether the ordering is worth money. ``vol_60`` has
    ic_t = +12.30, the largest in the ledger, and *negative* alpha against a cap-weighted market
    with a beta of 0.91-0.97: it orders names beautifully and loses. Reporting "best |t| 12.30 ->
    CLEARS" next to a low-volatility candidate was precisely the confident, correctly computed,
    entirely misleading number this engine exists to prevent.

    So ``clears_bar`` is now the firewall's ``verdict``, which is what it means for something to
    have passed: significance *and* deflated Sharpe *and* walk-forward *and* regime *and* costs
    *and* capacity. IC is still reported, labelled as ordering rather than profit, because it is
    genuinely informative about *why* something failed.
    """
    rows = con.execute("""
        SELECT COUNT(*), MAX(ABS(ic_t)), MAX(sharpe), MIN(verdict),
               MAX(CASE WHEN verdict = 'PROMOTE'
                          AND CAST(run_at AS DATE) >= ? THEN 1 ELSE 0 END),
               MAX(CASE WHEN verdict = 'PROMOTE' THEN 1 ELSE 0 END),
               MAX(CASE WHEN verdict = 'PROMOTE'
                        THEN CAST(run_at AS DATE) END)
        FROM evaluation_runs WHERE feature = ?
    """, [COST_MODEL_EPOCH, cond.feature]).fetchone()
    trials, best_ic_t, best_sharpe, verdict = rows[0] or 0, rows[1], rows[2], rows[3]
    promoted_current, promoted_ever, promoted_on = bool(rows[4]), bool(rows[5]), rows[6]
    if not trials:
        return {}
    # A pass reached under a superseded cost model is not a pass. Only a PROMOTE recorded at or
    # after the epoch counts as current.
    promoted = promoted_current
    stale = promoted_ever and not promoted_current
    return {
        "feature": cond.feature, "tested_horizon": cond.horizon,
        "trials_spent": trials,
        # Ordering ability, not profitability. Named so it cannot be mistaken for the latter.
        "best_abs_ic_t": best_ic_t,
        "best_net_sharpe": best_sharpe,
        "bonferroni_bar": bar["bar"], "ledger_trials": bar["trials"],
        # The firewall's own conclusion, which is the only thing that means "this worked".
        "verdict": "PROMOTE" if promoted else (verdict or "REJECT"),
        "clears_bar": promoted,
        "stale_promote": stale,
        "stale_promote_on": promoted_on if stale else None,
        "summary": _summary(trials, best_ic_t, verdict, promoted, stale, promoted_on, bar),
    }


def _summary(trials, best_ic_t, verdict, promoted, stale, promoted_on, bar) -> str:
    head = f"tested {trials}x; "
    if promoted:
        head += "firewall verdict PROMOTE, under the current cost model. "
    elif stale:
        head += (f"a PROMOTE was recorded on {promoted_on} under the superseded flat cost "
                 f"model and does not count; current verdict REJECT. ")
    else:
        head += f"firewall verdict {verdict or 'REJECT'}. "
    if best_ic_t is None:
        return head + "No IC recorded."
    return head + (
        f"Best IC |t| {best_ic_t:.2f} measures whether it *orders* names, not whether the "
        f"ordering pays; the bar of {bar['bar']:.2f} at {bar['trials']} trials applies to net "
        f"performance.")


def _oid(isin: str, key: str, on: date) -> str:
    h = hashlib.sha256(f"{isin}|{key}|{on}".encode()).hexdigest()[:12]
    return f"OPP-{on:%Y%m%d}-{h}"


def scan(con, *, as_of: date, keys: list[str] | None = None,
         min_adv: float = MIN_ADV_INR, limit_per_condition: int = 25) -> dict:
    """Which names satisfy which condition on ``as_of``, with priors and catalysts attached."""
    bar = bonferroni_bar(con)
    want = [conditions.BY_KEY[k] for k in keys] if keys else list(conditions.CONDITIONS)

    bd = con.execute("SELECT MAX(business_date) FROM features WHERE business_date <= ?",
                     [as_of]).fetchone()[0]
    if bd is None:
        return {"as_of": as_of, "note": "no features at or before this date",
                "opportunities": []}
    universe = con.execute("""
        SELECT COUNT(*) FROM features WHERE business_date = ? AND adv20 >= ?
    """, [bd, min_adv]).fetchone()[0]

    out: list[Opportunity] = []
    skipped: list[dict] = []
    for cond in want:
        pr = prior_for(con, cond, bar)
        if not pr:
            # Cannot happen while conditions.py only admits measured conditions; the check is
            # here so a future addition fails loudly rather than arriving without a record.
            skipped.append({"condition": cond.key,
                            "why": "no record in the trial ledger; not emitted"})
            continue

        if cond.key in conditions.RANKED:
            col, q, side = conditions.RANKED[cond.key]
            op = "<=" if side == "low" else ">="
            pct = q if side == "low" else q
            rows = con.execute(f"""
                WITH r AS (
                    SELECT lineage, {col} AS v, adv20,
                           PERCENT_RANK() OVER (ORDER BY {col}) AS pr
                    FROM features
                    WHERE business_date = ? AND adv20 >= ? AND {col} IS NOT NULL
                )
                SELECT lineage, v, pr FROM r
                WHERE pr {op} ? ORDER BY {'pr' if side == 'low' else 'pr DESC'}
                LIMIT ?
            """, [bd, min_adv, pct, limit_per_condition]).fetchall()
            hits = [(r[0], {"value": r[1], "percentile": r[2]}) for r in rows]
            n_hits = con.execute(f"""
                WITH r AS (SELECT PERCENT_RANK() OVER (ORDER BY {col}) AS pr
                           FROM features
                           WHERE business_date = ? AND adv20 >= ? AND {col} IS NOT NULL)
                SELECT COUNT(*) FROM r WHERE pr {op} ?
            """, [bd, min_adv, pct]).fetchone()[0]
        else:
            rows = con.execute(f"""
                SELECT lineage, adv20 FROM features
                WHERE business_date = ? AND adv20 >= ? AND ({cond.expr})
                ORDER BY adv20 DESC LIMIT ?
            """, [bd, min_adv, limit_per_condition]).fetchall()
            hits = [(r[0], {"adv20": r[1]}) for r in rows]
            n_hits = con.execute(f"""
                SELECT COUNT(*) FROM features
                WHERE business_date = ? AND adv20 >= ? AND ({cond.expr})
            """, [bd, min_adv]).fetchone()[0]

        share = n_hits / universe if universe else 0.0
        if share > MAX_UNIVERSE_SHARE:
            skipped.append({
                "condition": cond.key, "hits": n_hits, "share": share,
                "why": f"fires on {share:.0%} of the universe, which describes the market "
                       f"rather than a name - above the {MAX_UNIVERSE_SHARE:.0%} ceiling"})
            continue

        for lineage, ev in hits:
            meta = con.execute("""
                SELECT l.isin, (SELECT MAX(s.ticker) FROM security_listings s
                                WHERE s.isin = l.isin)
                FROM security_lineage l WHERE l.lineage = ? LIMIT 1
            """, [lineage]).fetchone()
            if not meta:
                continue
            isin, ticker = meta
            out.append(Opportunity(
                opportunity_id=_oid(isin, cond.key, bd), isin=isin, ticker=ticker,
                condition=cond.key, detected_on=bd, direction=cond.direction,
                claim=cond.claim, source=cond.source, prior=pr,
                evidence={"lineage": lineage, "condition_share_of_universe": share,
                          "names_satisfying": n_hits, **ev},
                caveat=cond.caveat, status="SCREENED"))

    return {"as_of": as_of, "features_date": bd, "universe": universe,
            "bonferroni": bar, "opportunities": out, "skipped": skipped}


def catalysts(con, isin: str, *, as_of: date) -> list[dict]:
    """Scheduled and recent events that could resolve an opportunity either way.

    Not a prediction of direction. A results date is a date on which the uncertainty gets
    smaller, which is what makes it a catalyst, and whether it resolves for or against is exactly
    what nobody knows in advance.
    """
    recent = con.execute("""
        SELECT business_date, event_type, materiality, headline
        FROM announcements
        WHERE isin = ? AND business_date <= ?
          AND business_date > CAST(? AS DATE) - INTERVAL 30 DAY
          AND materiality = 'high'
        ORDER BY business_date DESC LIMIT 5
    """, [isin, as_of, as_of]).fetchall()
    out = [{"kind": "recent_high_materiality", "date": r[0], "event_type": r[1],
            "headline": (r[3] or "")[:140]} for r in recent]
    # A quarterly cadence is inferable from the filing history, which is a weaker statement than
    # a calendar and is labelled as one.
    last = con.execute("""
        SELECT MAX(business_date) FROM announcements
        WHERE isin = ? AND event_type = 'RESULTS' AND business_date <= ?
    """, [isin, as_of]).fetchone()[0]
    if last:
        elapsed = (as_of - last).days
        due_in = 90 - elapsed
        out.append({"kind": "results_cadence", "last_results": last,
                    "sessions_since": elapsed,
                    "note": "next results inferred ~90 days after the last, from filing "
                            "history rather than a published calendar",
                    # A negative number here does not mean the results are in the past; it means
                    # the cadence inference has broken down, usually because the last RESULTS
                    # filing was missed or mis-typed. Reported as overdue rather than as a
                    # nonsense countdown.
                    "expected_window_days": due_in if due_in >= 0 else None,
                    "overdue_by_days": None if due_in >= 0 else -due_in,
                    "cadence_reliable": due_in >= -30})
    return out


def rank(opportunities: list[Opportunity]) -> list[Opportunity]:
    """Order by the strength of the *evidence*, not the strength of the signal.

    Deliberately blunt. The sort key is (passed the firewall, best net Sharpe, best IC |t|,
    liquidity), so a condition shown to *pay* outranks one that merely orders names well - which
    is a distinction the first version of this got backwards, ranking low volatility first on an
    IC of 12.30 that comes with negative alpha.
    With nothing in this archive clearing the bar, the first key is currently constant - which is
    the correct and slightly bleak behaviour, and preferable to a ranking that manufactures
    order out of signal magnitude.
    """
    return sorted(
        opportunities,
        key=lambda o: (
            not o.prior.get("clears_bar", False),
            # Net Sharpe before IC: ordering ability without net performance is what every
            # rejection in this ledger already has.
            -(o.prior.get("best_net_sharpe") or -9.9),
            -(o.prior.get("best_abs_ic_t") or 0.0),
            -(o.evidence.get("adv20") or 0.0),
        ))
