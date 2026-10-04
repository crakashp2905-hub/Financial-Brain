"""Capital follows evidence of edge, and leaves when the evidence does.

A strategy is not good or bad; it has an estimated edge with an uncertainty, and that estimate moves.
This turns a set of net-return histories into a capital allocation with four states:

    PAPER     too little history to say anything: no capital, but it keeps being measured
    ACTIVE    the decayed edge is distinguishable from zero: full Kelly-fraction capital
    REDUCE    some evidence but not much: half capital
    STOP      the edge has decayed to nothing or the sleeve is in a drawdown its own volatility cannot
              explain: no capital, kept on paper so a recovery is seen

## Three rules that matter

**Recency is weighted, not assumed.** Edge is estimated with exponential weights (half-life
:data:`HALF_LIFE` periods), and the t-statistic uses the *effective* sample size
``(sum w)^2 / sum w^2``, not the count. A strategy with ten years of history and a dead last two years
is a strategy whose recent estimate rests on a few observations, and the t says so.

**The mean is shrunk by its own t.** ``m_hat = m * max(0, 1 - 1/t^2)``: a sleeve at ``|t| = 1`` gets no
capital and one at ``t = 3`` keeps 89% of its mean. Selecting the best of many sleeves on a noisy
estimate and then trusting that estimate is the winner's curse; the shrinkage is what offsets it.

**Real money is a separate gate.** ``money_gate_passed`` must be true for a sleeve to receive anything
but paper capital, whatever its statistics say. The allocator never decides that a strategy is fit for
real money; it decides how a *validated* strategy is weighted against other validated strategies.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

HALF_LIFE = 36            #: periods (months, for monthly sleeves)
MIN_OBS = 24              #: fewer than this and the sleeve is on PAPER
T_ACTIVE = 2.0
T_REDUCE = 1.0
DD_SIGMAS = 3.0           #: a drawdown beyond this many sleeve-sigmas is not noise
KELLY_FRACTION = 0.25
MAX_SLEEVE = 0.40

PAPER, ACTIVE, REDUCE, STOP = "PAPER", "ACTIVE", "REDUCE", "STOP"


class AllocatorError(ValueError):
    pass


@dataclass(frozen=True)
class Sleeve:
    name: str
    returns: tuple                      #: net periodic returns, oldest first
    money_gate_passed: bool = False


def decayed_edge(returns, half_life: float = HALF_LIFE) -> dict:
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 3:
        raise AllocatorError(f"{len(r)} observations is not an edge estimate")
    age = np.arange(len(r))[::-1]
    w = 0.5 ** (age / half_life)
    mean = float(np.sum(w * r) / np.sum(w))
    var = float(np.sum(w * (r - mean) ** 2) / np.sum(w))
    n_eff = float(np.sum(w) ** 2 / np.sum(w ** 2))
    sd = var ** 0.5
    t = mean / (sd / np.sqrt(n_eff)) if sd > 0 else 0.0
    return {"mean": mean, "sd": sd, "n_eff": n_eff, "t": float(t), "n": int(len(r))}


def drawdown_sigmas(returns) -> float:
    """Worst current drawdown of the cumulative return, in units of the sleeve's own volatility."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 3 or r.std(ddof=1) == 0:
        return 0.0
    cum = np.cumsum(r)
    dd = float(np.max(np.maximum.accumulate(cum) - cum))
    return dd / float(r.std(ddof=1))


def state_of(sleeve: Sleeve) -> dict:
    e = decayed_edge(sleeve.returns)
    dd = drawdown_sigmas(sleeve.returns[-int(HALF_LIFE):])
    if e["n"] < MIN_OBS:
        st, why = PAPER, f"{e['n']} observations; {MIN_OBS} needed"
    elif e["mean"] <= 0 or e["t"] < T_REDUCE:
        st, why = STOP, f"decayed edge t = {e['t']:+.2f} is below {T_REDUCE}"
    elif dd > DD_SIGMAS * np.sqrt(HALF_LIFE):
        st, why = STOP, f"drawdown of {dd:.1f} sigma-periods is beyond noise"
    elif e["t"] < T_ACTIVE:
        st, why = REDUCE, f"decayed edge t = {e['t']:+.2f}"
    else:
        st, why = ACTIVE, f"decayed edge t = {e['t']:+.2f}"
    return {"state": st, "why": why, **e, "drawdown_sigmas": dd}


def allocate(sleeves: list[Sleeve], *, fraction: float = KELLY_FRACTION,
             cap: float = MAX_SLEEVE) -> dict:
    """Capital weights per sleeve, summing to at most 1; the remainder is cash."""
    if not sleeves:
        raise AllocatorError("no sleeves to allocate between")
    names = [s.name for s in sleeves]
    if len(set(names)) != len(names):
        raise AllocatorError("sleeve names must be unique")
    detail, raw = {}, {}
    for s in sleeves:
        st = state_of(s)
        shrink = max(0.0, 1.0 - 1.0 / st["t"] ** 2) if st["t"] > 0 else 0.0
        m_hat = st["mean"] * shrink
        want = 0.0
        if st["state"] in (ACTIVE, REDUCE) and st["sd"] > 0:
            want = fraction * m_hat / st["sd"] ** 2
            want *= 1.0 if st["state"] == ACTIVE else 0.5
        gated = want > 0 and not s.money_gate_passed
        detail[s.name] = {**st, "shrunk_mean": m_hat, "wanted": want,
                          "money_gate_passed": s.money_gate_passed,
                          "held_to_paper_by_money_gate": gated}
        raw[s.name] = 0.0 if gated else want
    raw = {k: min(v, cap) for k, v in raw.items()}
    total = sum(raw.values())
    if total > 1.0:
        raw = {k: v / total for k, v in raw.items()}
    paper = [k for k, d in detail.items() if d["state"] == PAPER or d["held_to_paper_by_money_gate"]]
    return {"weights": raw, "cash": 1.0 - sum(raw.values()), "paper_only": paper, "detail": detail}
