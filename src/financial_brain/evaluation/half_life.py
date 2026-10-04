"""How long a signal's information lasts, from its IC at increasing horizons.

Pre-registered in ``PRE-REGISTERED half-life and second-order spillover``. The IC of a signal at
horizon h is the rank correlation between the signal today and the return over the next h sessions. It
rises while information is still being realised and falls once the signal is stale, so the horizon of
its peak says how long to hold, and the horizon at which it has halved says how fast it decays. That in
turn sets how often a book built on it should turn over: a half-life of 10 sessions cannot be traded
monthly, and a half-life beyond 120 should not be traded weekly.

The half-life is measured **from the peak**, linearly interpolated, and reported as ``None`` when the IC
has not halved by the longest horizon, which is a statement ("longer than we can measure") rather than
a missing value.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from ..opportunity import engine as E
from ..opportunity import replay as R

HORIZONS = (1, 5, 10, 20, 40, 60, 120)
SIGNALS = tuple(c for c, _ in E.SIGNALS)


class HalfLifeError(ValueError):
    pass


def half_life(ic_by_h: dict[int, float]) -> dict:
    """Peak horizon and the horizon at which |IC| has halved, interpolated; None if it never does."""
    if len(ic_by_h) < 3:
        raise HalfLifeError("need at least three horizons to see a peak")
    hs = sorted(ic_by_h)
    sign = 1.0 if sum(ic_by_h.values()) >= 0 else -1.0
    v = np.array([sign * ic_by_h[h] for h in hs])
    k = int(np.argmax(v))
    peak = v[k]
    if peak <= 0:
        return {"peak_h": None, "peak_ic": float(peak), "half_life": None, "direction": sign,
                "note": "no positive IC at any horizon"}
    target = peak / 2.0
    hl = None
    for j in range(k + 1, len(hs)):
        if v[j] <= target:
            x0, x1, y0, y1 = hs[j - 1], hs[j], v[j - 1], v[j]
            hl = x0 + (y0 - target) / (y0 - y1) * (x1 - x0)
            break
    return {"peak_h": hs[k], "peak_ic": float(peak * sign), "half_life": (float(hl) if hl else None),
            "direction": sign, "still_rising": bool(k == len(hs) - 1),
            "note": "not halved by the longest horizon" if hl is None else ""}


def ic_table(con, *, start, end, horizons=HORIZONS, step: int = R.STEP, min_adv: float = E.MIN_ADV
             ) -> pd.DataFrame:
    """Per (session, signal, horizon) Spearman IC, signed so that positive means "the signal works"."""
    sess = R.sessions(con, start, end, step)
    px = con.execute("""SELECT lineage, business_date, close_adj FROM adjusted_prices
                        WHERE close_adj > 0 AND business_date >= ?""", [min(sess)]).df()
    wide = px.pivot_table(index="business_date", columns="lineage", values="close_adj")
    pos = {pd.Timestamp(d): i for i, d in enumerate(wide.index)}
    rows = []
    for s in sess:
        i = pos.get(pd.Timestamp(s))
        if i is None:
            continue
        try:
            cs = E.cross_section(con, s, min_adv=min_adv)
            cs["composite"] = E.composite(cs)
        except E.EngineError:
            continue
        cs = cs.set_index("lineage")
        for h in horizons:
            if i + h >= len(wide):
                continue
            fwd = (wide.iloc[i + h] / wide.iloc[i] - 1.0).reindex(cs.index)
            ok = fwd.notna()
            if ok.sum() < 100:
                continue
            for name, sign in list(E.SIGNALS) + [("composite", +1)]:
                rows.append((s, name, h, sign * spearmanr(cs.loc[ok, name], fwd[ok]).statistic))
    return pd.DataFrame(rows, columns=["session", "signal", "h", "ic"])


def summarise(table: pd.DataFrame) -> dict:
    out = {}
    for sig, g in table.groupby("signal"):
        mean = g.groupby("h")["ic"].mean().to_dict()
        t = {h: float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) for h, x in
             ((h, gg["ic"].dropna()) for h, gg in g.groupby("h")) if len(x) > 2}
        out[sig] = {"ic": {int(h): float(v) for h, v in mean.items()},
                    "t": {int(h): v for h, v in t.items()}, **half_life(mean)}
    return out
