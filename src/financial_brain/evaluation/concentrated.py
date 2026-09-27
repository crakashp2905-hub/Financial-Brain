"""A concentrated portfolio, judged by the same gates as a quintile (C22).

The firewall tests a signal the way a factor paper does: rank the universe, hold the top
fifth, rebalance. That is the right default - a quintile is hard to fool and easy to
compare - but it is not what a selective signal claims. The timing model accepts 2.7% of
cases; diluting it into a 20% bucket measures something the model never proposed, and
duly rejected it at -0.19% per period.

So this holds the top ``n`` names by signal instead, equal weighted, and charges cost on
the turnover it actually incurs - a concentrated book that keeps most of its names pays
less than the quintile's churn. Everything else is deliberately unchanged: the same
significance test against every trial ever run, the same deflated Sharpe, the same
walk-forward and regime gates, and a capacity gate that asks whether the names are liquid
enough to hold at the stated size.

Concentration is not a free parameter to search over. Each run counts as a trial, so
trying ten values makes all ten harder to pass, which is the intended cost of looking.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean

from ..costs.india import CostModel
from . import firewall

VERSION = "conc1"
MIN_REBALANCES = 24          # two years of monthly holdings before a verdict means much


def evaluate(con, feature: str, *, horizon: int = 20, top_n: int = 25,
             min_adv: float = 1e7, direction: int = 1, start: str | None = None,
             end: str | None = None) -> dict:
    """Hold the top ``n`` names by signal each rebalance; measure against the universe."""
    rows = con.execute(f"""
        WITH cal AS (
            SELECT business_date, ROW_NUMBER() OVER (ORDER BY business_date) AS k
            FROM (SELECT DISTINCT business_date FROM adjusted_prices)
        ), base AS (
            SELECT c.business_date, f.lineage, f.{feature} * {direction} AS x, c.k,
                   a.close_adj AS px,
                   (SELECT b.close_adj FROM adjusted_prices b
                    JOIN cal c2 ON c2.business_date = b.business_date
                    WHERE b.lineage = f.lineage AND c2.k = c.k + {horizon}) AS px_fwd
            FROM features f
            JOIN cal c USING (business_date)
            JOIN adjusted_prices a ON a.lineage = f.lineage
                                  AND a.business_date = f.business_date
            WHERE (c.k - 1) % {horizon} = 0 AND f.{feature} IS NOT NULL
              AND f.adv20 >= ? AND (? IS NULL OR c.business_date >= CAST(? AS DATE))
              AND (? IS NULL OR c.business_date <= CAST(? AS DATE))
        )
        SELECT business_date, lineage, x, px_fwd / px - 1 AS y
        FROM base WHERE px_fwd IS NOT NULL AND px > 0
        ORDER BY business_date, x DESC
        """, [min_adv, start, start, end, end]).fetchall()

    by_date: dict = defaultdict(list)
    for d, lineage, x, y in rows:
        by_date[d].append((float(x), lineage, float(y)))

    series, previous = [], set()
    for d in sorted(by_date):
        ranked = sorted(by_date[d], key=lambda r: -r[0])
        if len(ranked) < top_n:
            continue
        held = ranked[:top_n]
        names = {r[1] for r in held}
        universe = mean(r[2] for r in ranked)
        portfolio = mean(r[2] for r in held)
        turnover = 1.0 if not previous else 1 - len(names & previous) / top_n
        series.append({"date": d, "names": len(ranked), "turnover": turnover,
                       "portfolio": portfolio, "universe": universe,
                       "excess": portfolio - universe})
        previous = names
    return {"feature": feature, "top_n": top_n, "horizon": horizon, "series": series,
            "rebalances": len(series),
            "avg_names": mean(s["names"] for s in series) if series else 0}


def validate(con, feature: str, *, top_n: int = 25, horizon: int = 20,
             bucket: str = "mid", record: bool = True, **kw) -> dict:
    """The same gates the firewall applies, on a concentrated book."""
    r = evaluate(con, feature, horizon=horizon, top_n=top_n, **kw)
    series = r["series"]
    reasons, gates = [], {}
    if len(series) < MIN_REBALANCES:
        return {"verdict": "REJECT", "gates": {}, "top_n": top_n,
                "reasons": [f"only {len(series)} rebalances (needs {MIN_REBALANCES})"],
                "rebalances": len(series)}

    excess = [s["excess"] for s in series]
    prior = con.execute("SELECT COUNT(*), VAR_SAMP(sharpe) FROM evaluation_runs").fetchone()
    trials = (prior[0] or 0) + 1

    t = firewall._t(excess)
    p = 2 * (1 - firewall.N.cdf(abs(t)))
    gates["significance"] = p * trials < firewall.ALPHA
    if not gates["significance"]:
        reasons.append(f"excess t={t:+.2f}, p={p:.3g} x {trials} trials is not "
                       f"< {firewall.ALPHA}")

    turn = mean(s["turnover"] for s in series)
    cost = CostModel().round_trip(turnover=1_000_000, bucket=bucket)["bps"] / 10_000
    net = [s["excess"] - s["turnover"] * cost for s in series]
    gates["costs"] = mean(net) > 0
    if not gates["costs"]:
        reasons.append(f"net of costs {mean(net):+.2%} per period "
                       f"(turnover {turn:.0%}, round trip {cost:.2%})")

    sr_var = prior[1] if prior[1] is not None else (1 / max(len(net), 1))
    sr, dsr = firewall.deflated_sharpe(net, trials, sr_var)
    gates["deflated_sharpe"] = dsr >= firewall.MIN_DSR
    if not gates["deflated_sharpe"]:
        reasons.append(f"deflated Sharpe {dsr:.2f} < {firewall.MIN_DSR} after "
                       f"{trials} trials")

    by_year = defaultdict(list)
    for s in series:
        by_year[s["date"].year].append(s["excess"])
    sign = 1 if mean(excess) >= 0 else -1
    agree = (sum(1 for v in by_year.values() if mean(v) * sign > 0) / len(by_year)
             if by_year else 0)
    gates["walk_forward"] = agree >= firewall.MIN_YEAR_AGREEMENT
    if not gates["walk_forward"]:
        reasons.append(f"excess sign held in {agree:.0%} of years "
                       f"(< {firewall.MIN_YEAR_AGREEMENT:.0%})")

    from ..regime import brain as _regime
    regimes = _regime.series(con)
    by_regime = defaultdict(list)
    for s in series:
        by_regime[regimes.get(s["date"], "UNKNOWN")].append(s["excess"])
    against = {g: firewall._t(v) for g, v in by_regime.items()
               if firewall._t(v) * sign < -firewall.REGIME_T}
    gates["regime"] = not against
    if against:
        reasons.append("excess significantly reversed in " + ", ".join(
            f"{g} (t={t:+.1f})" for g, t in against.items()))

    gates["capacity"] = r["avg_names"] >= max(firewall.MIN_NAMES, top_n * 4)
    if not gates["capacity"]:
        reasons.append(f"only {r['avg_names']:.0f} names a rebalance to pick "
                       f"{top_n} from")

    verdict = "PROMOTE" if all(gates.values()) else "REJECT"
    out = {"verdict": verdict, "gates": gates, "reasons": reasons, "top_n": top_n,
           "rebalances": len(series), "mean_excess": mean(excess),
           "mean_net": mean(net), "turnover": turn, "sharpe": sr,
           "deflated_sharpe": dsr, "trials": trials}
    if record:
        con.execute("""INSERT INTO evaluation_runs (run_at, version, feature, horizon,
                       params, dates, mean_ic, ic_t, sharpe, deflated_sharpe, verdict,
                       reasons) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [datetime.now(timezone.utc), VERSION, f"{feature}@top{top_n}",
                     horizon, json.dumps({"top_n": top_n, "bucket": bucket,
                                          **{k: str(v) for k, v in kw.items()}}),
                     len(series), mean(excess), t, sr, dsr, verdict, reasons])
    return out


def threshold_book(con, feature: str, *, threshold: float, horizon: int = 20,
                   min_adv: float = 1e7, bucket: str = "mid", record: bool = True,
                   start: str | None = None, end: str | None = None) -> dict:
    """Hold only the names the signal actually accepts; hold cash when it accepts none.

    This is the construction a selective model proposes, and the only one that matches
    what its calibration measured. A quintile forces twenty percent of the universe into
    the book whatever the model thinks, and a top-N forces exactly N names even on days
    the model likes nothing - both measure a portfolio the model never asked for.

    Cash periods earn the benchmark by construction (excess zero) and pay no cost, which
    is the honest treatment: not trading is free, and the point of a selective signal is
    that it sometimes declines.
    """
    rows = con.execute(f"""
        WITH cal AS (
            SELECT business_date, ROW_NUMBER() OVER (ORDER BY business_date) AS k
            FROM (SELECT DISTINCT business_date FROM adjusted_prices)
        ), base AS (
            SELECT c.business_date, f.lineage, f.{feature} AS x, c.k, a.close_adj AS px,
                   (SELECT b.close_adj FROM adjusted_prices b
                    JOIN cal c2 ON c2.business_date = b.business_date
                    WHERE b.lineage = f.lineage AND c2.k = c.k + {horizon}) AS px_fwd
            FROM features f
            JOIN cal c USING (business_date)
            JOIN adjusted_prices a ON a.lineage = f.lineage
                                  AND a.business_date = f.business_date
            WHERE (c.k - 1) % {horizon} = 0 AND f.{feature} IS NOT NULL
              AND f.adv20 >= ? AND (? IS NULL OR c.business_date >= CAST(? AS DATE))
              AND (? IS NULL OR c.business_date <= CAST(? AS DATE))
        )
        SELECT business_date, lineage, x, px_fwd / px - 1 AS y
        FROM base WHERE px_fwd IS NOT NULL AND px > 0
        """, [min_adv, start, start, end, end]).fetchall()

    by_date: dict = defaultdict(list)
    for d, lineage, x, y in rows:
        by_date[d].append((float(x), lineage, float(y)))

    cost = CostModel().round_trip(turnover=1_000_000, bucket=bucket)["bps"] / 10_000
    series, previous = [], set()
    for d in sorted(by_date):
        everything = by_date[d]
        held = [r for r in everything if r[0] >= threshold]
        universe = mean(r[2] for r in everything)
        if not held:
            series.append({"date": d, "held": 0, "turnover": 0.0, "excess": 0.0,
                           "net": 0.0, "invested": False})
            previous = set()
            continue
        names = {r[1] for r in held}
        turnover = 1.0 if not previous else 1 - len(names & previous) / len(names)
        excess = mean(r[2] for r in held) - universe
        series.append({"date": d, "held": len(held), "turnover": turnover,
                       "excess": excess, "net": excess - turnover * cost,
                       "invested": True})
        previous = names

    invested = [s for s in series if s["invested"]]
    net = [s["net"] for s in series]
    out = {"feature": feature, "threshold": threshold, "rebalances": len(series),
           "invested_rebalances": len(invested),
           "time_invested": len(invested) / len(series) if series else 0,
           "mean_held": mean(s["held"] for s in invested) if invested else 0,
           "mean_excess_when_invested": mean(s["excess"] for s in invested) if invested else 0,
           "mean_net_overall": mean(net) if net else 0,
           "turnover_when_invested": mean(s["turnover"] for s in invested) if invested else 0}
    if invested:
        t = firewall._t([s["excess"] for s in invested])
        prior = con.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0] or 0
        trials = prior + 1
        p = 2 * (1 - firewall.N.cdf(abs(t)))
        out["excess_t"] = t
        out["significant_after_trials"] = bool(p * trials < firewall.ALPHA)
        out["trials"] = trials
        out["verdict"] = ("PROMOTE" if (out["significant_after_trials"]
                                        and out["mean_net_overall"] > 0) else "REJECT")
    else:
        out["verdict"] = "REJECT"
        out["reason"] = "the signal never accepted anything in this window"
    if record:
        con.execute("""INSERT INTO evaluation_runs (run_at, version, feature, horizon,
                       params, dates, mean_ic, ic_t, sharpe, deflated_sharpe, verdict,
                       reasons) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [datetime.now(timezone.utc), VERSION,
                     f"{feature}@threshold{threshold:.3f}", horizon,
                     json.dumps({"threshold": threshold, "bucket": bucket}),
                     len(series), out["mean_excess_when_invested"],
                     out.get("excess_t"), None, None, out["verdict"], []])
    return out
