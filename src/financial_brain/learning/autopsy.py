"""Why did this trade lose? An autopsy of every closed trade, from the path it actually took.

The hypothesis loop asks *which situations* lose. This asks *how* each loss happened, which is a different question with a
different use: it needs no statistics (it is arithmetic on one trade) and it shows where the money leaks even when no pattern is
confirmable yet. The path comes from ``quote_archive``: every poll of the name from entry to the close, the same prices the engine
acted on.

For each trade, in units of its own risk R (the distance from the fill to the stop):

    MFE / MAE        the best and worst the trade ever was while open
    after the exit   how far the original direction went before the close (did the stop sell the bottom?)

A losing trade then gets exactly one **primary cause**, by this order (the first that applies):

    COST_DOMINATED   the signal made money before charges (gross > 0) and costs turned it into a loss
    NOISE_STOP       stopped out, then price went on to reach at least ``NOISE_R`` R in the trade's favour the same day
    GAVE_BACK        was at least ``GAVE_BACK_R`` R in profit and still lost: the exit design returned the gain
    NEVER_WORKED     stopped out and never got ``WORKED_R`` R in profit: the entry was wrong from the start
    STOPPED          any other stop: the market moved against it and stayed there
    TIME_LOSS        held to the time exit and lost
    OTHER            anything else

Wins are labelled WIN. Secondary flags (WIDE_SPREAD, HIGH_SLIPPAGE) are recorded beside the primary cause. The thresholds are
fixed constants with the reasoning above, not fitted to this ledger. A trade without an archive path gets only the causes that
need no path (COST_DOMINATED, STOPPED, TIME_LOSS) and ``has_path = False``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

NOISE_R = 1.0              # price reached 1R in favour after the stop: the direction was right, the stop was noise
GAVE_BACK_R = 1.0          # the trade was 1R up at its best
WORKED_R = 0.25            # below this the trade never really moved the right way
WIDE_SPREAD_BP = 5.0       # top quartile of the quoted spread in live trading
HIGH_SLIPPAGE_BP = 10.0    # between the 75th and 90th percentile of implementation shortfall
PRIMARY = ("COST_DOMINATED", "NOISE_STOP", "GAVE_BACK", "NEVER_WORKED", "STOPPED", "TIME_LOSS", "OTHER")


def _naive(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize(None) if t.tzinfo is not None else t


def load_paths(archive, trades: pd.DataFrame) -> dict[tuple[str, str], tuple[np.ndarray, np.ndarray]]:
    """(day, symbol) -> (poll times, prices) from the quote archive, only for names traded that day."""
    paths: dict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = {}
    for day, g in trades.groupby("day"):
        syms = sorted(g["symbol"].unique())
        q = archive.execute(f"""SELECT symbol, ts, ltp FROM quotes WHERE CAST(ts AS DATE) = CAST(? AS DATE)
                               AND ltp IS NOT NULL AND symbol IN ({','.join('?' * len(syms))}) ORDER BY symbol, ts""",
                            [day, *syms]).df()
        for sym, h in q.groupby("symbol"):
            paths[(day, sym)] = (h["ts"].to_numpy(dtype="datetime64[ns]"), h["ltp"].to_numpy(dtype=float))
    return paths


def classify(r, path: tuple[np.ndarray, np.ndarray] | None) -> dict:
    """One trade's autopsy. ``r``: a row with the ledger's trade columns."""
    sign = 1.0 if r.side == "long" else -1.0
    risk = abs(r.entry_px - r.stop)
    out = {"trade_id": r.trade_id, "mode": r.mode, "day": r.day, "symbol": r.symbol, "strategy": r.strategy, "side": r.side,
           "net_pnl": float(r.net_pnl), "gross_pnl": float(r.gross_pnl), "exit_reason": r.exit_reason,
           "net_bp": float(r.net_pnl / (r.entry_px * r.qty) * 1e4), "r_gross": float(r.gross_pnl / (risk * r.qty)) if risk else np.nan,
           "mae_r": np.nan, "mfe_r": np.nan, "minutes_in": None, "reversed_after_exit": None, "has_path": False}
    t0, t1 = _naive(r.entry_ts), _naive(r.exit_ts)
    out["minutes_in"] = int(round((t1 - t0).total_seconds() / 60))
    reversed_after = None
    if path is not None and risk > 0 and len(path[0]):
        ts, px = path
        i0, i1 = np.searchsorted(ts, t0.to_datetime64(), "left"), np.searchsorted(ts, t1.to_datetime64(), "right")
        seg, after = px[i0:i1], px[i1:]
        if len(seg):
            fav = sign * (seg - r.entry_px) / risk
            out["mfe_r"], out["mae_r"] = float(max(0.0, fav.max())), float(max(0.0, (-fav).max()))
            out["has_path"] = True
            if len(after):
                reversed_after = bool((sign * (after - r.entry_px) / risk).max() >= NOISE_R)
            else:
                reversed_after = False
    out["reversed_after_exit"] = reversed_after
    flags = []
    if r.net_pnl >= 0:
        primary = "WIN"
    else:
        stop = r.exit_reason == "stop"
        if r.gross_pnl > 0:
            primary = "COST_DOMINATED"
        elif stop and reversed_after:
            primary = "NOISE_STOP"
        elif out["has_path"] and out["mfe_r"] >= GAVE_BACK_R:
            primary = "GAVE_BACK"
        elif stop and out["has_path"] and out["mfe_r"] < WORKED_R:
            primary = "NEVER_WORKED"
        elif stop:
            primary = "STOPPED"
        elif r.exit_reason == "time":
            primary = "TIME_LOSS"
        else:
            primary = "OTHER"
        flags.append(primary)
    if pd.notna(getattr(r, "spread_bp", np.nan)) and r.spread_bp >= WIDE_SPREAD_BP:
        flags.append("WIDE_SPREAD")
    if pd.notna(getattr(r, "shortfall_bp", np.nan)) and r.shortfall_bp >= HIGH_SLIPPAGE_BP:
        flags.append("HIGH_SLIPPAGE")
    out["primary_class"] = primary
    out["classes"] = ",".join(flags)
    return out


def build(trades: pd.DataFrame, archive=None) -> pd.DataFrame:
    """Autopsy every closed trade in ``trades`` (ledger rows). ``archive``: an open quote-archive connection, or None."""
    if trades.empty:
        return pd.DataFrame()
    paths = load_paths(archive, trades) if archive is not None else {}
    rows = [classify(r, paths.get((r.day, r.symbol))) for r in trades.itertuples()]
    return pd.DataFrame(rows)


def leaks(auto: pd.DataFrame) -> pd.DataFrame:
    """Where the losses come from: per primary cause, trades, rupees lost, share of all losses, mean bp."""
    lost = auto[auto["net_pnl"] < 0]
    if lost.empty:
        return pd.DataFrame(columns=["cause", "trades", "lost_inr", "share", "mean_bp"])
    total = -lost["net_pnl"].sum()
    g = lost.groupby("primary_class").agg(trades=("net_pnl", "size"), lost_inr=("net_pnl", lambda s: -s.sum()),
                                          mean_bp=("net_bp", "mean")).reset_index().rename(columns={"primary_class": "cause"})
    g["share"] = g["lost_inr"] / total
    g["order"] = g["cause"].map({c: i for i, c in enumerate(PRIMARY)})
    return g.sort_values("lost_inr", ascending=False).drop(columns="order").reset_index(drop=True)


def summary(auto: pd.DataFrame) -> dict:
    """Headline numbers: how many trades, how many lost, how much, and the path coverage behind the classes."""
    if auto.empty:
        return {"trades": 0}
    lost = auto[auto["net_pnl"] < 0]
    return {"trades": int(len(auto)), "losers": int(len(lost)), "lost_inr": float(-lost["net_pnl"].sum()),
            "won_inr": float(auto.loc[auto["net_pnl"] > 0, "net_pnl"].sum()), "net_inr": float(auto["net_pnl"].sum()),
            "with_path": float(auto["has_path"].mean()), "wide_spread": int(auto["classes"].str.contains("WIDE_SPREAD").sum()),
            "high_slippage": int(auto["classes"].str.contains("HIGH_SLIPPAGE").sum())}


def render(auto: pd.DataFrame) -> str:
    s = summary(auto)
    if not s["trades"]:
        return "Autopsy: no closed trades yet."
    lk = leaks(auto)
    lines = [f"Autopsy of {s['trades']} closed trades ({s['with_path']:.0%} with a price path): {s['losers']} lost "
             f"Rs {s['lost_inr']:,.0f}, the rest won Rs {s['won_inr']:,.0f}; net Rs {s['net_inr']:,.0f}.",
             f"{'primary cause':16s} {'trades':>6s} {'lost Rs':>11s} {'share':>6s} {'mean bp':>8s}"]
    for r in lk.itertuples():
        lines.append(f"{r.cause:16s} {r.trades:6d} {r.lost_inr:11,.0f} {r.share:6.0%} {r.mean_bp:+8.1f}")
    lines.append(f"Also flagged: {s['wide_spread']} trades with a spread of {WIDE_SPREAD_BP:.0f} bp or more, "
                 f"{s['high_slippage']} with {HIGH_SLIPPAGE_BP:.0f} bp or more of slippage.")
    return "\n".join(lines)
