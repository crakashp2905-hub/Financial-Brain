"""Alpha Validation Firewall (ARCHITECTURE.md §9) - the only thing allowed to say "yes".

    "The AI is not allowed to declare itself successful. The validation layer does that."

A signal is PROMOTED only if it clears every gate; any failure REJECTS it with the
reasons. Gates, on non-overlapping rebalances from ``benchmark.evaluate``:

``significance``     IC t-stat, Bonferroni-adjusted for *every trial ever run*
                     (``evaluation_runs`` counts them - re-running variations until one
                     passes is exactly what this blocks)
``deflated_sharpe``  Bailey & Lopez de Prado: probability the net Sharpe beats the best
                     Sharpe expected from that many trials of pure noise
``walk_forward``     the IC keeps its sign in most calendar years, not one lucky stretch
``regime``           no market regime where the IC is significantly the other way
``costs``            the long-only top quintile (India: shorting cash equity is not an
                     option) still beats the universe after round-trip costs on its
                     measured turnover, each rebalance charged its *own* holdings' costs
                     (``costs/book.py``) rather than one bucket for the whole book
``capacity``         enough names per rebalance to form a quintile worth holding

Thresholds are module constants, versioned with the code; changing them is a code change
with a reason, never a parameter tweaked until something passes.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from statistics import NormalDist, mean, stdev

from ..costs import book
from ..costs.india import CostModel
from . import benchmark

VERSION = "fw1"
ALPHA = 0.05
MIN_DSR = 0.95
MIN_YEAR_AGREEMENT = 0.70
MIN_NAMES = 100
REGIME_T = 2.0
N = NormalDist()
_GAMMA = 0.5772156649                      # Euler-Mascheroni


def _t(xs):
    return mean(xs) / stdev(xs) * math.sqrt(len(xs)) if len(xs) > 2 and stdev(xs) > 0 else 0.0


def _moments(xs):
    m, s = mean(xs), stdev(xs)
    if s == 0:
        return 0.0, 3.0
    n = len(xs)
    return (sum((x - m) ** 3 for x in xs) / n / s ** 3,
            sum((x - m) ** 4 for x in xs) / n / s ** 4)


def expected_max_sharpe(trials: int, sr_var: float) -> float:
    """Expected maximum Sharpe among ``trials`` independent noise strategies."""
    if trials < 2:
        return 0.0
    return math.sqrt(sr_var) * ((1 - _GAMMA) * N.inv_cdf(1 - 1 / trials)
                                + _GAMMA * N.inv_cdf(1 - 1 / (trials * math.e)))


def deflated_sharpe(returns: list[float], trials: int, sr_var: float) -> tuple[float, float]:
    """(per-period Sharpe, probability it exceeds the noise benchmark)."""
    if len(returns) < 3 or stdev(returns) == 0:
        return 0.0, 0.0
    sr = mean(returns) / stdev(returns)
    skew, kurt = _moments(returns)
    sr0 = expected_max_sharpe(trials, sr_var)
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denom <= 0:
        return sr, 0.0
    return sr, N.cdf((sr - sr0) * math.sqrt(len(returns) - 1) / math.sqrt(denom))


def _turnover(series) -> float:
    prev, changes = None, []
    for r in series:
        cur = set(r["top"] or [])
        if prev is not None and cur:
            changes.append(1 - len(cur & prev) / len(cur))
        prev = cur
    return mean(changes) if changes else 1.0


def validate(con, feature: str, horizon: int = 20, *, bucket: str = "mid",
             record: bool = True, **kw) -> dict:
    r = benchmark.evaluate(con, feature, horizon, **kw)
    series = [s for s in r["series"] if s["ic"] is not None]
    ics = [s["ic"] for s in series]
    reasons, gates = [], {}

    # Multiple testing: this run is one more trial.
    prior = con.execute("SELECT COUNT(*), VAR_SAMP(sharpe) FROM evaluation_runs").fetchone()
    trials = (prior[0] or 0) + 1
    ic_t = _t(ics)
    p = 2 * (1 - N.cdf(abs(ic_t)))
    gates["significance"] = p * trials < ALPHA
    if not gates["significance"]:
        reasons.append(f"IC t={ic_t:+.2f}, p={p:.3g} x {trials} trials is not < {ALPHA}")

    # Costs on the long-only top quintile's measured turnover.
    #
    # Each rebalance is charged what *its own holdings* cost, not one bucket for the whole
    # book (``costs/book.py``). Every signal's top quintile here is 45-52% small or micro,
    # so the flat ``mid`` charge of 70.87 bps was optimistic by a third to a half - the
    # error ran in favour of the strategies, which is the direction that matters. The flat
    # number is kept alongside so the size of the correction stays visible.
    turn = _turnover(series)
    flat = CostModel().round_trip(turnover=1_000_000, bucket=bucket)["bps"] / 10_000
    held = book.per_rebalance(con, series)
    pairs = [(s["top_excess"], c if c is not None else flat)
             for s, c in zip(series, held) if s["top_excess"] is not None]
    net = [x - turn * c for x, c in pairs]
    cost = mean([c for _, c in pairs]) if pairs else flat
    gates["costs"] = bool(net) and mean(net) > 0
    if not gates["costs"]:
        reasons.append(f"top quintile net of costs {mean(net) if net else float('nan'):+.2%}"
                       f" per period (turnover {turn:.0%}, round trip {cost:.2%} on the"
                       f" book's own liquidity mix, {flat:.2%} flat)")

    sr_var = prior[1] if prior[1] is not None else (1 / max(len(net), 1))
    sr, dsr = deflated_sharpe(net, trials, sr_var)
    gates["deflated_sharpe"] = dsr >= MIN_DSR
    if not gates["deflated_sharpe"]:
        reasons.append(f"deflated Sharpe {dsr:.2f} < {MIN_DSR} after {trials} trials")

    by_year = defaultdict(list)
    for s in series:
        by_year[s["date"].year].append(s["ic"])
    sign = 1 if mean(ics or [0]) >= 0 else -1
    years = {y: mean(v) for y, v in by_year.items()}
    agree = sum(1 for v in years.values() if v * sign > 0) / len(years) if years else 0
    gates["walk_forward"] = agree >= MIN_YEAR_AGREEMENT
    if not gates["walk_forward"]:
        reasons.append(f"IC sign held in {agree:.0%} of years (< {MIN_YEAR_AGREEMENT:.0%})")

    from ..regime import brain as _regime
    regimes = _regime.series(con)
    by_reg = defaultdict(list)
    for s in series:
        by_reg[regimes.get(s["date"], "UNKNOWN")].append(s["ic"])
    against = {g: _t(v) for g, v in by_reg.items() if _t(v) * sign < -REGIME_T}
    gates["regime"] = not against
    if against:
        reasons.append("IC significantly reversed in " + ", ".join(
            f"{g} (t={t:+.1f})" for g, t in against.items()))

    names = r["avg_names"]
    gates["capacity"] = names >= MIN_NAMES
    if not gates["capacity"]:
        reasons.append(f"{names:.0f} names per rebalance < {MIN_NAMES}")

    verdict = "PROMOTE" if all(gates.values()) and series else "REJECT"
    if not series:
        reasons.append("no rebalance dates with outcomes")
    out = {"version": VERSION, "feature": feature, "horizon": horizon, "verdict": verdict,
           "gates": gates, "reasons": reasons, "trials": trials, "dates": len(series),
           "mean_ic": mean(ics) if ics else float("nan"), "ic_t": ic_t, "sharpe": sr,
           "deflated_sharpe": dsr, "turnover": turn, "net_per_period": mean(net) if net else
           float("nan"), "round_trip": cost, "round_trip_flat": flat,
           "bucket_mix": book.mix(con, series), "ic_by_year": years,
           "ic_by_regime": {g: mean(v) for g, v in by_reg.items()}}
    if record:
        # The IC series is persisted, not just its summary. Two trials on the same idea have
        # highly correlated IC series, and that correlation is the only way to tell how many
        # independent tests 150 trials really are (evaluation/families.py). Storing the t-statistic
        # alone made that unmeasurable and the effective count could only be bounded to [7, 150].
        con.execute("""INSERT INTO evaluation_runs (run_at, version, feature, horizon, params,
                       dates, mean_ic, ic_t, sharpe, deflated_sharpe, verdict, reasons,
                       ic_series, ic_dates)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [datetime.now(timezone.utc), VERSION, feature, horizon,
                     json.dumps({"bucket": bucket, **{k: str(v) for k, v in kw.items()}}),
                     len(series), out["mean_ic"], ic_t, sr, dsr, verdict, reasons,
                     ics, [s["date"] for s in series]])
    return out
