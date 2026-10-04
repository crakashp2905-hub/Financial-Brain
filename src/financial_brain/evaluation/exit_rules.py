"""Do stops help? Simulating exit rules on daily closes against holding to the rebalance.

Pre-registered in ``PRE-REGISTERED market timing and exit rules``. Each position is entered at a close and
held for ``horizon`` sessions. A rule watches the **daily closes** after entry; the first close that
breaches it is the exit, taken at that close (so gap-through-the-stop slippage is *under*stated: a real
stop order would fill at or below the trigger). After an exit the money earns the cash rate to the end of
the window. The hold-to-rebalance sale happens anyway, so costs are the same and are not charged to either
side.

    stop      exit when the close is ``x`` below the entry close
    trailing  exit when the close is ``x`` below the highest close since entry
"""
from __future__ import annotations

import numpy as np

RULES = {"S8": ("stop", 0.08), "S12": ("stop", 0.12), "S15": ("stop", 0.15),
         "T10": ("trail", 0.10), "T15": ("trail", 0.15), "T20": ("trail", 0.20)}
CASH_RATE = 0.06
DAYS = 252


class ExitError(ValueError):
    pass


def simulate(paths: np.ndarray, rule: tuple[str, float], *, cash_rate: float = CASH_RATE) -> dict:
    """``paths``: (horizon+1) x names matrix of closes, row 0 the entry close. Returns per-name return and
    whether the rule fired."""
    if paths.ndim != 2 or paths.shape[0] < 3:
        raise ExitError("paths must be (horizon+1) x names with at least 2 sessions after entry")
    kind, x = rule
    rel = paths / paths[0] - 1.0                       # return since entry, per day
    if kind == "stop":
        hit = rel[1:] <= -x
    elif kind == "trail":
        peak = np.maximum.accumulate(paths, axis=0)
        hit = (paths / peak - 1.0)[1:] <= -x
    else:
        raise ExitError(f"unknown rule kind {kind!r}")
    fired = hit.any(axis=0)
    first = np.where(fired, hit.argmax(axis=0), paths.shape[0] - 2)      # index into rows 1..H
    h = paths.shape[0] - 1
    cash_day = (1 + cash_rate) ** (1 / DAYS) - 1
    days_left = (h - 1) - first                                         # sessions still to run after the exit
    exit_ret = rel[1:][first, np.arange(paths.shape[1])]
    ret = np.where(fired, (1 + exit_ret) * (1 + cash_day) ** days_left - 1.0, rel[-1])
    return {"ret": ret, "fired": fired, "hold": rel[-1]}


def left_tail(x: np.ndarray, q: float = 0.05) -> float:
    return float(np.nanquantile(x, q))
