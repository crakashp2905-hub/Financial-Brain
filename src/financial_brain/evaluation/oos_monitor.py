"""G5 of ADR-0004: is a registered strategy's live paper record behaving like its backtest?

Each trial registered under ADR-0004 runs in the paper zoo from the session after its data cutoff. This compares that
record with the backtest the registration judged:

    band        at k sessions after the start, the backtest's 2022-26 per-session distribution gives
                mean*k +/- 1.645*sd*sqrt(k) - the 90% range a cumulative live net excess should sit in
    early stop  three consecutive month ends below the band's lower edge: FAILED_EARLY
    verdict     12 rebalances after the cutoff with a positive net mean: CONFIRMED; with a non-positive one: FAILED;
                before that: PENDING

The per-session moments come from per-period ones (mean/h, sd/sqrt(h)), which assumes the period's sessions are
independent - stated, and conservative in the sense that it widens nothing that the data would narrow.

``execution_drift`` reports the intraday paper runner's measured fills against the costs it assumed, which is the
other half of "is the live record what the backtest said": a strategy can be right and still lose to slippage.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

Z90 = 1.645
MIN_REBALANCES = 12
EARLY_STOP_MONTHS = 3
SPLIT = pd.Timestamp("2022-01-01")

PENDING, CONFIRMED, FAILED, FAILED_EARLY = "PENDING", "CONFIRMED", "FAILED", "FAILED_EARLY"


@dataclass(frozen=True)
class Registration:
    strategy: str
    prereg: str
    cutoff: str                 #: last session of data the registration saw
    hold: int                   #: sessions per rebalance
    backtest: str               #: parquet of the per-period net series the registration judged
    column: str = "net"


REGISTERED = (
    Registration("edge_m1_momentum_book", "PRE-REGISTERED cost-aware momentum book", "2026-10-12", 40,
                 "data/edges/m1_net.parquet"),
    Registration("edge_f3_short_buildup", "PRE-REGISTERED short build-up avoid filter", "2026-10-12", 20,
                 "data/edges/f3_short_net.parquet"),
)


def band(backtest: pd.Series, hold: int) -> tuple[float, float]:
    """Per-session mean and sd from the backtest's 2022-26 periods."""
    s = backtest.dropna()
    s.index = pd.to_datetime(s.index)
    b = s[s.index >= SPLIT]
    if len(b) < 3:
        raise ValueError("the backtest has too few 2022-26 periods to set a band")
    return float(b.mean() / hold), float(b.std(ddof=1) / np.sqrt(hold))


def assess(live: pd.DataFrame, backtest: pd.Series, reg: Registration) -> dict:
    """``live``: the zoo's rows for the strategy (day, net, bench), from the session after the cutoff."""
    mu, sd = band(backtest, reg.hold)
    live = live.copy()
    live["day"] = pd.to_datetime(live["day"])
    live = live[live["day"] > pd.Timestamp(reg.cutoff)].sort_values("day")
    live = live.iloc[1:] if len(live) else live              # the first evening only sets weights
    if live.empty:
        return {"strategy": reg.strategy, "verdict": PENDING, "sessions": 0, "rebalances": 0, "months": [],
                "why": "no settled live sessions yet"}
    x = (live["net"] - live["bench"].fillna(0)).to_numpy()
    cum = np.cumsum(x)
    k = np.arange(1, len(x) + 1)
    lo, hi = mu * k - Z90 * sd * np.sqrt(k), mu * k + Z90 * sd * np.sqrt(k)
    frame = pd.DataFrame({"day": live["day"].to_numpy(), "cum": cum, "lo": lo, "hi": hi})
    month_ends = frame.groupby(frame["day"].dt.to_period("M")).tail(1)
    months, run = [], 0
    for r in month_ends.itertuples():
        where = "below" if r.cum < r.lo else "above" if r.cum > r.hi else "inside"
        run = run + 1 if where == "below" else 0
        months.append({"month": str(r.day.to_period("M")), "cum": float(r.cum), "lo": float(r.lo), "hi": float(r.hi),
                       "where": where})
    rebalances = len(x) // reg.hold
    if run >= EARLY_STOP_MONTHS:
        verdict, why = FAILED_EARLY, f"{run} consecutive month ends below the backtest's 90% band"
    elif rebalances >= MIN_REBALANCES:
        verdict = CONFIRMED if x.mean() > 0 else FAILED
        why = f"{rebalances} rebalances, mean net excess {x.mean() * 1e4:+.2f} bp a session"
    else:
        verdict, why = PENDING, f"{rebalances} of {MIN_REBALANCES} rebalances"
    return {"strategy": reg.strategy, "verdict": verdict, "why": why, "sessions": len(x), "rebalances": rebalances,
            "cum": float(cum[-1]), "band_now": (float(lo[-1]), float(hi[-1])), "months": months,
            "per_session_backtest": (mu, sd)}


def report(zoo_con, regs=REGISTERED) -> list[dict]:
    out = []
    for reg in regs:
        if not Path(reg.backtest).exists():
            out.append({"strategy": reg.strategy, "verdict": PENDING, "why": f"no backtest at {reg.backtest}"})
            continue
        bt = pd.read_parquet(reg.backtest)[reg.column]
        live = zoo_con.execute("SELECT day, net, bench FROM zoo_pnl WHERE strategy = ? ORDER BY day",
                               [reg.strategy]).df()
        out.append(assess(live, bt, reg))
    return out


def execution_drift(live_con, *, mode: str = "live") -> dict:
    """Measured fills of the intraday paper runner against what its cost model assumed, per trade, in bp."""
    df = live_con.execute("""SELECT strategy, bucket, spread_bp, shortfall_bp, entry_walk_bp, costs, qty, entry_px
                             FROM trades WHERE mode = ? AND exit_ts IS NOT NULL""", [mode]).df()
    if df.empty:
        return {"trades": 0}
    df["modelled_bp"] = df["costs"] / (df["qty"] * df["entry_px"]) * 1e4
    g = df.groupby("strategy").agg(trades=("qty", "size"), modelled_bp=("modelled_bp", "mean"),
                                   spread_bp=("spread_bp", "mean"), shortfall_bp=("shortfall_bp", "mean"),
                                   walk_bp=("entry_walk_bp", "mean"))
    # Shortfall is the decision price to the fill, both legs - the part the flat cost model does not charge. The
    # quoted spread is shown beside it as context, not added (the shortfall already contains the half it crossed).
    g["shortfall_vs_model"] = g["shortfall_bp"] / g["modelled_bp"]
    return {"trades": int(len(df)), "by_strategy": g.reset_index().to_dict("records")}


def render(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        lines.append(f"{r['strategy']:28s} {r['verdict']:13s} {r.get('why', '')}")
        for m in r.get("months", [])[-6:]:
            lines.append(f"    {m['month']}  cum {m['cum'] * 100:+6.2f}%  band [{m['lo'] * 100:+6.2f}%, "
                         f"{m['hi'] * 100:+6.2f}%]  {m['where']}")
    return "\n".join(lines)
