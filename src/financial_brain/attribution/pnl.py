"""Where the money came from, as an identity rather than an opinion.

``attribution/move.py`` decomposes one name's *price move* into market, peer and specific. This module
decomposes a paper run's *profit and loss*, which is a different question and the one that decides what
to keep building: after a run makes or loses money, which part of the machine did it.

## Why the decomposition is done in logs

Multi-period attribution has a well-known trap. Per session the algebra is exact:

    r_book = r_benchmark + (r_universe - r_benchmark) + (r_gross - r_universe) + (r_book - r_gross)
             ^market       ^universe selection         ^stock selection         ^costs

but returns compound rather than add, so summing each component's daily contributions does **not**
reproduce the compounded total. The gap is a real number with no owner, and every arithmetic
attribution has to either smooth it across the components (Cariño, Menchero) or leave it as a
residual that grows with the window. Over eleven years it is not small.

Logs are exactly additive in both directions at once - across components within a session, and across
sessions - because each component is a ratio:

    ln(1 + r_book) = ln(1 + r_bench)
                   + ln((1 + r_univ)  / (1 + r_bench))
                   + ln((1 + r_gross) / (1 + r_univ))
                   + ln((1 + r_book)  / (1 + r_gross))

So the log decomposition is reported as the primary one, and it closes to floating-point precision.
The arithmetic version is given alongside for readability, with the compounding interaction shown as
its own line rather than absorbed anywhere.

## Why concentration is reported next to everything else

A run that made money on three names is three bets, not a strategy, whatever its Sharpe says. The
share of total P&L coming from the top few positions is the cheapest available check on whether a
result is a distribution or an anecdote, and nothing else in this project asks it.
"""
from __future__ import annotations

import math
from datetime import date


class AttributionError(ValueError):
    pass


def _universe_daily(con, *, sessions: list[date], min_adv: float, eligible: set | None,
                    rebalance: int) -> dict[date, float]:
    """Daily return of an equal-weight universe portfolio **rebalanced like the strategy**.

    The first version averaged each session's cross-sectional returns and chained that. It is the
    obvious thing and it is wrong in a way that matters more than the whole strategy: compounding a
    daily arithmetic mean harvests the cross-sectional variance of 1,700 names at zero cost, so it
    reports a universe nobody could hold. On this database it made the eleven-year universe
    **+583.33%** where the control, chained at the strategy's own rebalance frequency, says
    **+328.76%**. A stock-selection term measured against the inflated bar is understated by the
    entire difference.

    So the universe is formed equal-weight at each rebalance and then **held**: share counts are
    fixed within a period and weights drift with prices, which is what an equal-weight book actually
    does. The daily return is the change in that portfolio's value. Chaining these reproduces the
    control's definition while still giving one number per session, which is what a daily
    decomposition needs.
    """
    if not sessions:
        return {}
    cal = sorted(set(sessions))
    bounds = cal[::rebalance]
    if bounds[-1] != cal[-1]:
        bounds.append(cal[-1])

    out: dict[date, float] = {}
    for d0, d1 in zip(bounds, bounds[1:]):
        rows = con.execute("""
            WITH universe AS (
                SELECT f.lineage FROM features f
                WHERE f.business_date = (SELECT MAX(business_date) FROM features
                                         WHERE business_date <= ?)
                  AND f.adv20 >= ? AND (? IS FALSE OR f.lineage IN (SELECT UNNEST(?)))
            ), anchor AS (
                SELECT p.lineage, p.close_adj AS px0
                FROM adjusted_prices p JOIN universe USING (lineage)
                WHERE p.business_date = ? AND p.close_adj > 0
            )
            -- Fixed share counts from the period's open, so weights drift exactly as a held book's
            -- do. One rupee per name at the open makes the value the mean of price ratios.
            SELECT p.business_date, AVG(p.close_adj / a.px0), COUNT(*)
            FROM adjusted_prices p JOIN anchor a USING (lineage)
            WHERE p.business_date >= ? AND p.business_date <= ? AND p.close_adj > 0
            GROUP BY p.business_date
            ORDER BY p.business_date
        """, [d0, min_adv, eligible is not None,
              sorted(eligible) if eligible else [], d0, d0, d1]).fetchall()
        prev = None
        for session, value, _n in rows:
            if prev is not None and prev > 0:
                out[session] = value / prev - 1
            prev = value
    return out


def _benchmark_daily(con, *, sessions: list[date],
                     benchmark: str = "Nifty 500") -> dict[date, float]:
    rows = con.execute("""
        SELECT business_date, close_level / LAG(close_level) OVER (ORDER BY business_date) - 1
        FROM index_levels
        WHERE index_name = ? AND business_date BETWEEN ? AND ? AND close_level > 0
    """, [benchmark, min(sessions), max(sessions)]).fetchall()
    return {r[0]: r[1] for r in rows if r[1] is not None}


def decompose(con, run, *, min_adv: float = 1e7, benchmark: str = "Nifty 500",
              eligible: set | None = None) -> dict:
    """Split a run's return into market, universe selection, stock selection and costs.

    ``eligible`` is the universe the run was allowed to trade. A ``Run`` records only its size and a
    hash of it - enough to tell two experiments apart, not enough to rebuild the set - so the caller
    has to hand back the same set it passed to ``engine.run``. Passing nothing measures the universe
    term against the whole liquid market, which is a different and usually larger number; the result
    says which was used.

    The log columns close to floating point: ``residual`` is the check, not a component, and anything
    above ``1e-9`` means the decomposition is not an identity and should not be read.
    """
    eq = run.equity
    if len(eq) < 2:
        raise AttributionError("a decomposition needs at least two sessions")

    sessions = [e["session"] for e in eq]
    declared = run.config.get("universe_size")
    if eligible is not None and declared and len(eligible) != declared:
        raise AttributionError(
            f"the run traded a universe of {declared} lineages and {len(eligible)} were passed; "
            f"a universe term measured against a different universe is not an attribution")
    univ = _universe_daily(con, sessions=sessions, min_adv=min_adv, eligible=eligible,
                           rebalance=int(run.config.get("rebalance") or 20))
    bench = _benchmark_daily(con, sessions=sessions, benchmark=benchmark)

    # Costs paid on each session, so the gross book return can be recovered.
    cost_on: dict[date, float] = {}
    for t in run.trades:
        cost_on[t.session] = cost_on.get(t.session, 0.0) + t.cost_inr

    parts = {k: 0.0 for k in ("market", "universe_selection", "stock_selection", "costs")}
    arith = dict(parts)
    used = skipped = 0
    for prev, cur in zip(eq, eq[1:]):
        e0, e1 = prev["equity"], cur["equity"]
        if e0 <= 0 or e1 <= 0:
            skipped += 1
            continue
        r_book = e1 / e0 - 1
        cost = cost_on.get(cur["session"], 0.0)
        # The book's return before the costs it paid that session.
        r_gross = (e1 + cost) / e0 - 1
        r_univ = univ.get(cur["session"])
        r_bench = bench.get(cur["session"])
        if r_univ is None or r_bench is None:
            skipped += 1
            continue
        if min(1 + r_book, 1 + r_gross, 1 + r_univ, 1 + r_bench) <= 0:
            skipped += 1
            continue

        parts["market"] += math.log(1 + r_bench)
        parts["universe_selection"] += math.log((1 + r_univ) / (1 + r_bench))
        parts["stock_selection"] += math.log((1 + r_gross) / (1 + r_univ))
        parts["costs"] += math.log((1 + r_book) / (1 + r_gross))

        arith["market"] += r_bench
        arith["universe_selection"] += r_univ - r_bench
        arith["stock_selection"] += r_gross - r_univ
        arith["costs"] += r_book - r_gross
        used += 1

    if not used:
        raise AttributionError(
            "no session had a book return, a universe return and a benchmark level together")

    total_log = sum(parts.values())
    # The identity check: the components must rebuild the compounded return over the sessions used.
    grown = 1.0
    for prev, cur in zip(eq, eq[1:]):
        if cur["session"] in univ and cur["session"] in bench and prev["equity"] > 0:
            grown *= cur["equity"] / prev["equity"]
    residual = total_log - math.log(grown) if grown > 0 else float("nan")

    total_arith = sum(arith.values())
    compounded = math.exp(total_log) - 1
    return {
        "sessions_used": used,
        "sessions_skipped": skipped,
        "log": parts,
        "log_total": total_log,
        "as_return": {k: math.exp(v) - 1 for k, v in parts.items()},
        "arithmetic": arith,
        "arithmetic_total": total_arith,
        "compounded_total": compounded,
        # Not a component. The gap between adding daily arithmetic contributions and compounding
        # them, which is why the log columns are the ones to read.
        "compounding_interaction": compounded - total_arith,
        "residual": residual,
        "closes": abs(residual) < 1e-9,
        "universe_total": math.exp(parts["market"] + parts["universe_selection"]) - 1,
        "universe_total_control": run.control.get("universe_return"),
        "benchmark": benchmark,
        "universe_measured_on": ("the run's own eligible set" if eligible is not None
                                 else "the whole liquid market (no eligible set was passed)"),
        "why": "log contributions are additive across components and across sessions, so they close "
               "to floating point; the arithmetic columns do not and their gap is reported as "
               "compounding_interaction rather than spread over the components",
    }


def by_name(con, run) -> dict:
    """Realised and unrealised P&L per lineage, and how concentrated the total is.

    Realised comes from the trades: rupees out on sells minus rupees in on buys, costs included.
    Unrealised is whatever is still held at the last session, marked at its close. A name whose
    position closed exactly has all of its P&L in the realised column.
    """
    if not run.equity:
        raise AttributionError("the run has no equity curve")
    last = run.equity[-1]["session"]

    flows: dict[str, dict] = {}
    for t in run.trades:
        f = flows.setdefault(t.lineage, {"bought": 0.0, "sold": 0.0, "costs": 0.0,
                                         "shares": 0, "trades": 0})
        if t.side == "BUY":
            f["bought"] += t.notional
            f["shares"] += t.shares
        else:
            f["sold"] += t.notional
            f["shares"] -= t.shares
        f["costs"] += t.cost_inr
        f["trades"] += 1

    out = []
    for lineage, f in flows.items():
        mark = None
        if f["shares"] > 0:
            row = con.execute("""SELECT close_adj FROM adjusted_prices
                                 WHERE lineage = ? AND business_date <= ? AND close_adj > 0
                                 ORDER BY business_date DESC LIMIT 1""",
                              [lineage, last]).fetchone()
            mark = row[0] if row else None
        held_value = (f["shares"] * mark) if (f["shares"] > 0 and mark) else 0.0
        realised = f["sold"] - f["bought"] - f["costs"]
        out.append({"lineage": lineage, "pnl": realised + held_value,
                    "realised_and_flows": realised, "held_value": held_value,
                    "open_shares": f["shares"], "costs": f["costs"],
                    "trades": f["trades"],
                    "marked": mark is not None or f["shares"] == 0})
    out.sort(key=lambda r: r["pnl"], reverse=True)

    gross_gain = sum(r["pnl"] for r in out if r["pnl"] > 0)
    total = sum(r["pnl"] for r in out)
    winners = [r for r in out if r["pnl"] > 0]
    return {
        "names": out,
        "total_pnl": total,
        "gross_gain": gross_gain,
        "gross_loss": sum(r["pnl"] for r in out if r["pnl"] < 0),
        "n_names": len(out),
        "n_winners": len(winners),
        "hit_rate": (len(winners) / len(out)) if out else float("nan"),
        "unmarked": [r["lineage"] for r in out if not r["marked"]],
        "concentration": concentration(out),
        "why": "P&L is cash flows plus the mark on whatever is still held, so a name whose position "
               "closed has all of it in realised_and_flows",
    }


def concentration(names: list[dict], tops=(1, 3, 5, 10)) -> dict:
    """What share of the gains the best few names produced.

    Reported against **gross gains**, not net P&L: a net total near zero makes every share explode,
    and the question is whether the winning came from a few names, which the losers do not change.
    A run whose top three names produced most of the gains is three bets.
    """
    gains = sorted((r["pnl"] for r in names if r["pnl"] > 0), reverse=True)
    total = sum(gains)
    out = {"gross_gain": total, "n_winners": len(gains)}
    for k in tops:
        out[f"top{k}_share"] = (sum(gains[:k]) / total) if total > 0 else None
    # Where half the gains came from: one name means an anecdote, many means a distribution.
    half, acc = None, 0.0
    for i, g in enumerate(gains, 1):
        acc += g
        if total > 0 and acc >= total / 2:
            half = i
            break
    out["names_for_half_the_gains"] = half
    out["why"] = ("shares are of gross gains; a run whose top few names produced most of them is "
                  "that many bets, whatever its Sharpe says")
    return out


def execution(run) -> dict:
    """What execution cost, separately from what brokerage cost.

    Implementation shortfall is achieved-against-decision, and the decision was made the previous
    session, so this is the drift between the price the signal was computed at and the price actually
    achieved. Only simulated fills carry it; close fills are achieved *at* the close by construction
    and would report zero, which is an absence of measurement rather than an absence of slippage.
    """
    measured = [t for t in run.trades if t.shortfall_bps is not None]
    unfilled = [t for t in run.trades if t.unfilled]
    notional = sum(t.notional for t in measured)
    rupees = sum(t.notional * (t.shortfall_bps or 0.0) / 1e4 for t in measured)
    return {
        "fills_measured": len(measured),
        "fills_total": len(run.trades),
        "measured_share": (len(measured) / len(run.trades)) if run.trades else float("nan"),
        "notional_measured": notional,
        "shortfall_inr": rupees,
        "mean_shortfall_bps": (rupees / notional * 1e4) if notional else None,
        "brokerage_inr": sum(t.cost_inr for t in run.trades),
        "unfilled_orders": len(unfilled),
        "unfilled_shares": sum(t.unfilled for t in unfilled),
        "why": "shortfall is measured only on simulated fills; a close-filled run reports no "
               "shortfall because it was not measured, not because there was none",
    }


def report(con, run, *, min_adv: float = 1e7, benchmark: str = "Nifty 500",
           eligible: set | None = None) -> dict:
    """The three attributions together, which is how they should be read."""
    return {"decomposition": decompose(con, run, min_adv=min_adv, benchmark=benchmark,
                                       eligible=eligible),
            "by_name": by_name(con, run),
            "execution": execution(run)}
