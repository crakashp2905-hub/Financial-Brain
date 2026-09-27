"""Research reads: the trial ledger, the Bonferroni bar, and what has been tried.

The ledger is the most important thing this project holds and the easiest to misread, so the
service reports it with the arithmetic already done. A caller asking "has momentum worked" gets
the verdict, the bar *as it stands now*, and an explicit statement that the IC t-statistic is not
a claim about profit - because the one time that distinction was left to the caller, the
opportunity engine reported CLEARS for a signal with negative alpha.
"""
from __future__ import annotations

from datetime import date
from statistics import NormalDist

from ..opportunity import conditions, discovery
from . import NOT_FOUND, ServiceError

N = NormalDist()
ALPHA = 0.05


def bar(con, *, extra: int = 1) -> dict:
    """The |t| a new result must clear, from the ledger as it stands.

    Recomputed rather than stored. The bar rises with every trial, so one quoted from when a test
    was run is out of date the moment anything else is tested - which is exactly how a stale
    PROMOTE came to sit in this ledger.
    """
    n = con.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0]
    total = n + extra
    return {"trials_recorded": n, "counting_this_one": total,
            "bar": N.inv_cdf(1 - ALPHA / (2 * total)), "alpha": ALPHA,
            "basis": "two-sided Bonferroni over every trial ever recorded"}


def ledger(con, *, feature: str | None = None, limit: int = 50) -> dict:
    """Trials, newest first, with the current bar attached to every row."""
    b = bar(con, extra=0)
    rows = con.execute("""
        SELECT run_at, feature, horizon, dates, mean_ic, ic_t, sharpe, deflated_sharpe,
               verdict, params
        FROM evaluation_runs
        WHERE (? IS NULL OR feature = ?)
        ORDER BY run_at DESC LIMIT ?
    """, [feature, feature, limit]).fetchall()
    if feature and not rows:
        raise ServiceError(NOT_FOUND, f"no trials recorded for {feature!r}")
    return {
        "bar": b,
        "note": "ic_t is the t-statistic of the information coefficient: whether the signal "
                "orders names, not whether the ordering pays. The bar applies to net "
                "performance, and the verdict is the firewall's own conclusion.",
        "trials": [{"run_at": r[0], "feature": r[1], "horizon": r[2], "rebalances": r[3],
                    "mean_ic": r[4], "ic_t": r[5], "net_sharpe": r[6],
                    "deflated_sharpe": r[7], "verdict": r[8], "params": r[9],
                    "ic_t_exceeds_bar": bool(r[5] is not None and abs(r[5]) > b["bar"])}
                   for r in rows],
    }


def what_has_been_tried(con) -> dict:
    """Every feature in the ledger, its best result, and whether it ever passed.

    The summary this project needs most often, and the one a reader gets wrong without help: 150
    trials, one recorded PROMOTE, and that PROMOTE was reached under a cost model since corrected.
    """
    b = bar(con, extra=0)
    rows = con.execute("""
        SELECT feature, COUNT(*), MAX(ABS(ic_t)), MAX(sharpe),
               MAX(CASE WHEN verdict = 'PROMOTE' THEN 1 ELSE 0 END),
               MAX(CASE WHEN verdict = 'PROMOTE' THEN CAST(run_at AS DATE) END),
               MIN(CAST(run_at AS DATE)), MAX(CAST(run_at AS DATE))
        FROM evaluation_runs GROUP BY feature ORDER BY COUNT(*) DESC
    """).fetchall()
    out = []
    for f, n, ict, sh, prom, prom_on, first, last in rows:
        stale = bool(prom) and prom_on is not None and prom_on < discovery.COST_MODEL_EPOCH
        out.append({
            "feature": f, "trials": n, "best_abs_ic_t": ict, "best_net_sharpe": sh,
            "ever_promoted": bool(prom), "promoted_on": prom_on,
            "promote_is_stale": stale,
            "current_verdict": ("PROMOTE" if (prom and not stale) else "REJECT"),
            "first_tested": first, "last_tested": last,
        })
    return {
        "bar": b,
        "features": out,
        "totals": {
            "features": len(out),
            "trials": sum(r["trials"] for r in out),
            "currently_promoted": sum(1 for r in out
                                      if r["current_verdict"] == "PROMOTE"),
            "stale_promotes": sum(1 for r in out if r["promote_is_stale"]),
        },
        "cost_model_epoch": discovery.COST_MODEL_EPOCH,
        "epoch_note": "verdicts before this date were reached under a flat cost model that "
                      "charged one impact bucket to a whole book; they need re-validation.",
    }


def registered_conditions(con) -> list[dict]:
    """The conditions the opportunity engine may screen for, each with its measured record."""
    b = discovery.bonferroni_bar(con)
    out = []
    for c in conditions.CONDITIONS:
        pr = discovery.prior_for(con, c, b)
        out.append({"key": c.key, "claim": c.claim, "source": c.source,
                    "direction": c.direction, "tested_horizon": c.horizon,
                    "caveat": c.caveat, "prior": pr})
    return out


def scan(con, *, as_of: date, keys: list[str] | None = None,
         limit_per_condition: int = 10) -> dict:
    """Which names satisfy which measured condition, ranked by evidence rather than signal."""
    r = discovery.scan(con, as_of=as_of, keys=keys,
                       limit_per_condition=limit_per_condition)
    opps = discovery.rank(r.get("opportunities", []))
    return {
        "as_of": r["as_of"], "features_date": r.get("features_date"),
        "universe": r.get("universe"), "bonferroni": r.get("bonferroni"),
        "skipped_conditions": r.get("skipped", []),
        "clearing_the_bar": sum(1 for o in opps if o.prior.get("clears_bar")),
        "opportunities": [o.as_dict() for o in opps],
    }
