"""Trial 11, ``PRE-REGISTERED intraday and swing family on minute bars``: nine rules on 2016-2026 one-minute bars of
F&O stocks, traded as stock futures.

Per symbol, ``scan_symbol`` reads its minute bars once and emits every rule's events; per day it also keeps what the
cross-sectional pieces need (first and last half-hour returns for I2; a 15-minute price grid for the intraday null;
daily OHLCV for the swing rules and their equal-weighted hedge). ``assemble`` turns those into per-rule event tables
with outcomes, ``monthly`` into period series, ``null`` into G4.

Conventions fixed by the registration: entry at the close of the bar that completes a signal; intraday exits at the
close of the 15:15 bar (I2 at 15:29); intraday cost 10 bp a leg; swing outcomes hedged with the equal-weighted F&O
basket at 20 bp.

Stated approximation (G4 only): a random intraday entry is taken on the 15-minute price grid nearest the real entry
time rather than the exact minute - the grid is what fits in memory for 795 names over ten years.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LEG = 0.0010
SWING_COST = 0.0020
GRID = ["09:15", "09:30", "09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30", "11:45", "12:00",
        "12:15", "12:30", "12:45", "13:00", "13:15", "13:30", "13:45", "14:00", "14:15", "14:30", "14:45", "15:00",
        "15:15"]
INTRADAY = ("I1_gap_and_go", "I3_surge_15m", "I4_surge_60m", "I5_gap_fade")
SWING = ("S1_high_volume_up", "S2_high_volume_down", "S3_pullback_uptrend", "S4_hourly_rsi_dip")


def _hm(ts: pd.Series) -> pd.Series:
    return ts.dt.strftime("%H:%M")


def _rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d).clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn)


def _bars(day: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Session bars of ``minutes`` from 09:15 (labelled by their start), from one day's minute bars."""
    g = day.set_index("ts")
    r = g.resample(f"{minutes}min", origin=g.index[0].normalize() + pd.Timedelta(hours=9, minutes=15), label="left")
    out = pd.DataFrame({"o": r["open"].first(), "h": r["high"].max(), "l": r["low"].min(), "c": r["close"].last(),
                        "v": r["volume"].sum()}).dropna()
    return out


def scan_symbol(sym: str, m: pd.DataFrame, fno_days: set) -> dict:
    """Every rule's events for one symbol, plus the per-day pieces the cross-sectional rules and nulls need.

    ``m``: columns ts (tz-aware), open, high, low, close, volume, sorted. ``fno_days``: sessions (Timestamp, naive
    midnight) on which this symbol had a live near-month future."""
    m = m.copy()
    m["d"] = m["ts"].dt.tz_localize(None).dt.normalize()
    m["hm"] = _hm(m["ts"])
    events = {r: [] for r in (*INTRADAY, "S4_hourly_rsi_dip")}
    halves, grid_rows, daily_rows = [], [], []
    slot15: dict[str, list] = {}
    slot60: dict[str, list] = {}
    prev_close = None
    hourly_closes: list[tuple[pd.Timestamp, float]] = []
    for d, day in m.groupby("d", sort=True):
        day = day[day["hm"] <= "15:29"]
        if len(day) < 300:
            prev_close = float(day["close"].iloc[-1]) if len(day) else prev_close
            continue
        o, c_last = float(day["open"].iloc[0]), float(day["close"].iloc[-1])
        daily_rows.append({"d": d, "o": o, "h": float(day["high"].max()), "l": float(day["low"].min()), "c": c_last,
                           "v": float(day["volume"].sum())})
        close_at = day.set_index("hm")["close"]
        grid = close_at.reindex(GRID).ffill()
        grid_rows.append({"d": d, **{k: float(v) for k, v in grid.items()}})
        upto = close_at[close_at.index <= "15:15"]
        if upto.empty:
            prev_close = c_last
            continue
        exit_1515 = float(upto.iloc[-1])
        in_fno = d in fno_days
        # I2 pieces: first half hour and last half hour
        try:
            first = float(close_at[close_at.index <= "09:44"].iloc[-1]) / o - 1
            last = c_last / float(close_at[close_at.index <= "14:59"].iloc[-1]) - 1
            if in_fno:
                halves.append({"d": d, "first": first, "last": last})
        except IndexError:
            pass
        if prev_close and in_fno:
            gap = o / prev_close - 1
            # I1: first close beyond the 09:15-09:29 range in the gap's direction
            if abs(gap) >= 0.01:
                rng = day[day["hm"] <= "09:29"]
                hi, lo = rng["high"].max(), rng["low"].min()
                after = day[(day["hm"] >= "09:30") & (day["hm"] <= "15:14")]
                side = 1 if gap > 0 else -1
                hit = after[after["close"] > hi] if side > 0 else after[after["close"] < lo]
                if len(hit):
                    e = hit.iloc[0]
                    events["I1_gap_and_go"].append({"d": d, "sym": sym, "side": side, "t_in": e["hm"],
                                                    "gross": side * (exit_1515 / e["close"] - 1)})
            # I5: fade a gap of 2% or more from 09:20
            early = day[day["hm"] <= "09:19"]
            if abs(gap) >= 0.02 and len(early):           # a stock that opened late (halt, pre-open) has no 09:20 entry
                e = early.iloc[-1]
                side = -1 if gap > 0 else 1
                events["I5_gap_fade"].append({"d": d, "sym": sym, "side": side, "t_in": "09:20",
                                              "gross": side * (exit_1515 / e["close"] - 1)})
        # I3 / I4: volume surges against the same slot's previous 20 sessions
        for minutes, store, vmult, rmin, rule, hold in ((15, slot15, 3.0, 0.01, "I3_surge_15m", 4),
                                                         (60, slot60, 3.0, 0.015, "I4_surge_60m", None)):
            b = _bars(day, minutes)
            b["hm"] = b.index.strftime("%H:%M")
            busy_until = -1
            for i, (_t, row) in enumerate(b.iterrows()):
                hist = store.setdefault(row["hm"], [])
                if in_fno and len(hist) >= 20 and i > busy_until and row["hm"] < "15:15":
                    base = np.mean(hist[-20:])
                    ret = row["c"] / row["o"] - 1
                    if base > 0 and row["v"] >= vmult * base and abs(ret) >= rmin:
                        side = 1 if ret > 0 else -1
                        if hold is None:
                            x = exit_1515
                            busy_until = len(b)
                        else:
                            j = min(i + hold, len(b) - 1)
                            while j > i and b["hm"].iloc[j] > "15:15":
                                j -= 1
                            if j == i:
                                hist.append(row["v"])
                                continue
                            x = float(b["c"].iloc[j])
                            busy_until = j
                        events[rule].append({"d": d, "sym": sym, "side": side, "t_in": row["hm"],
                                             "gross": side * (x / row["c"] - 1)})
                hist.append(row["v"])
        # hourly closes for S4
        for t, row in _bars(day, 60).iterrows():
            hourly_closes.append((t, float(row["c"]), d))
        prev_close = c_last
    daily = pd.DataFrame(daily_rows).set_index("d") if daily_rows else pd.DataFrame()
    # S4 on 60-minute bars: RSI(14) < 25 with the previous session's close above its 200-session mean
    if len(hourly_closes) > 50 and len(daily) > 200:
        h = pd.DataFrame(hourly_closes, columns=["t", "c", "d"])
        h["rsi"] = _rsi(h["c"])
        sma200_prev = daily["c"].rolling(200, min_periods=200).mean().shift(1)
        above = (daily["c"].shift(1) > sma200_prev)
        sess = list(daily.index)
        pos = {s: i for i, s in enumerate(sess)}
        i, n = 0, len(h)
        while i < n:
            r = h.iloc[i]
            if r["rsi"] < 25 and r["d"] in pos and bool(above.get(r["d"], False)) and r["d"] in fno_days:
                limit_day = sess[min(pos[r["d"]] + 3, len(sess) - 1)]
                j = i + 1
                while j < n and h["rsi"].iloc[j] <= 50 and h["d"].iloc[j] <= limit_day:
                    j += 1
                j = min(j, n - 1)
                events["S4_hourly_rsi_dip"].append({"d": r["d"], "sym": sym, "side": 1, "d_out": h["d"].iloc[j],
                                                    "gross": h["c"].iloc[j] / r["c"] - 1})
                i = j + 1
                continue
            i += 1
    grid = pd.DataFrame(grid_rows).set_index("d") if grid_rows else pd.DataFrame()
    return {"events": events, "halves": halves, "grid": grid, "daily": daily}


def swing_events(daily: dict[str, pd.DataFrame], fno: dict[str, set]) -> dict[str, list]:
    """S1-S3 from each symbol's daily bars (built from its minutes); held five sessions."""
    out = {"S1_high_volume_up": [], "S2_high_volume_down": [], "S3_pullback_uptrend": []}
    for sym, D in daily.items():
        if len(D) < 60:
            continue
        v20 = D["v"].rolling(20, min_periods=20).mean().shift(1)
        rng = (D["h"] - D["l"]).replace(0, np.nan)
        loc = (D["c"] - D["l"]) / rng
        sma50 = D["c"].rolling(50, min_periods=50).mean()
        down = D["c"] < D["c"].shift(1)
        quiet = D["v"] < v20
        idx = list(D.index)
        last = {k: -10 for k in out}
        for i in range(len(idx) - 5):
            d = idx[i]
            if d not in fno.get(sym, ()):
                continue
            fwd = D["c"].iloc[i + 5] / D["c"].iloc[i] - 1
            hv = np.isfinite(v20.iloc[i]) and D["v"].iloc[i] >= 3 * v20.iloc[i]
            cases = {"S1_high_volume_up": (hv and loc.iloc[i] >= 0.75, 1),
                     "S2_high_volume_down": (hv and loc.iloc[i] <= 0.25, -1),
                     "S3_pullback_uptrend": (i >= 3 and np.isfinite(sma50.iloc[i]) and D["c"].iloc[i] > sma50.iloc[i]
                                             and bool(down.iloc[i - 2:i + 1].all()) and bool(quiet.iloc[i - 2:i + 1].all()), 1)}
            for rule, (fire, side) in cases.items():
                if fire and i - last[rule] >= 5:
                    out[rule].append({"d": d, "sym": sym, "side": side, "d_out": idx[i + 5], "gross_raw": fwd})
                    last[rule] = i
    return out


def ew_returns(daily: dict[str, pd.DataFrame], fno: dict[str, set]) -> pd.Series:
    """Equal-weighted F&O close-to-close return each session (the swing hedge)."""
    R = pd.DataFrame({s: D["c"].pct_change() for s, D in daily.items()})
    M = pd.DataFrame({s: pd.Series(R.index.isin(list(fno.get(s, ()))), index=R.index) for s in R.columns})
    return R.where(M).mean(axis=1)


def assemble(scans: dict[str, dict], fno: dict[str, set]) -> dict[str, pd.DataFrame]:
    """Per-rule event tables with ``out`` (net), from the per-symbol scans."""
    out: dict[str, pd.DataFrame] = {}
    for rule in INTRADAY:
        ev = pd.DataFrame([e for s in scans.values() for e in s["events"][rule]])
        if len(ev):
            ev["out"] = ev["gross"] - LEG
        out[rule] = ev
    # I2: one long-short event a day across names
    H = pd.DataFrame([dict(h, sym=s) for s, sc in scans.items() for h in sc["halves"]])
    rows = []
    if len(H):
        for d, g in H.groupby("d"):
            if len(g) < 50:
                continue
            hi, lo = g["first"].quantile(0.9), g["first"].quantile(0.1)
            spread = g.loc[g["first"] >= hi, "last"].mean() - g.loc[g["first"] <= lo, "last"].mean()
            rows.append({"d": d, "gross": spread, "out": spread - 2 * LEG})
    out["I2_first_to_last_half_hour"] = pd.DataFrame(rows)
    daily = {s: sc["daily"] for s, sc in scans.items() if len(sc["daily"])}
    ew = ew_returns(daily, fno)
    cum = (1 + ew.fillna(0)).cumprod()
    sw = swing_events(daily, fno)
    sw["S4_hourly_rsi_dip"] = [e for sc in scans.values() for e in sc["events"]["S4_hourly_rsi_dip"]]
    for rule, evs in sw.items():
        ev = pd.DataFrame(evs)
        if len(ev):
            bench = cum.reindex(ev["d_out"]).to_numpy() / cum.reindex(ev["d"]).to_numpy() - 1
            raw = ev["gross_raw"] if "gross_raw" in ev else ev["gross"]
            ev["gross"] = ev["side"] * (raw - bench)
            ev["out"] = ev["gross"] - SWING_COST
        out[rule] = ev
    return out


def monthly(ev: pd.DataFrame, col: str = "out") -> pd.Series:
    if ev is None or ev.empty:
        return pd.Series(dtype=float)
    d = pd.to_datetime(ev["d"])
    s = ev[col].groupby(d.dt.to_period("M")).mean()
    s.index = s.index.to_timestamp()
    return s


# ------------------------------------------------------------------ G4: same times, random F&O names

def grid_frame(scans: dict[str, dict]) -> pd.DataFrame:
    parts = {s: sc["grid"] for s, sc in scans.items() if len(sc["grid"])}
    if not parts:
        return pd.DataFrame()
    G = pd.concat(parts, names=["sym", "d"])
    return G.reorder_levels(["d", "sym"]).sort_index()


def _grid_at(t: str) -> str:
    """The grid point at or after ``t`` (a random entry is taken there)."""
    for g in GRID:
        if g >= t:
            return g
    return GRID[-1]


def null(rule: str, ev: pd.DataFrame, scans: dict[str, dict], fno: dict[str, set], *, draws: int = 200,
         seed: int = 20261015) -> dict:
    from . import standard as S
    rng = np.random.default_rng(seed)
    real = S.replication(monthly(ev))["stat"]
    sims = []
    if rule == "I2_first_to_last_half_hour":
        H = pd.DataFrame([dict(h, sym=s) for s, sc in scans.items() for h in sc["halves"]])
        days = {d: g for d, g in H.groupby("d") if len(g) >= 50}
        for _ in range(draws):
            rows = []
            for d, g in days.items():
                f = rng.permutation(g["first"].to_numpy())
                hi, lo = np.quantile(f, 0.9), np.quantile(f, 0.1)
                rows.append({"d": d, "out": g["last"].to_numpy()[f >= hi].mean() - g["last"].to_numpy()[f <= lo].mean()
                             - 2 * LEG})
            sims.append(S.replication(monthly(pd.DataFrame(rows)))["stat"])
    elif rule in INTRADAY:
        G = grid_frame(scans)
        by_day = {d: g.droplevel(0) for d, g in G.groupby(level=0)}
        for _ in range(draws):
            rows = []
            for r in ev.itertuples():
                g = by_day.get(r.d)
                if g is None:
                    continue
                cand = [s for s in g.index if r.d in fno.get(s, ())]
                if not cand:
                    continue
                x = g.loc[cand[rng.integers(len(cand))]]
                t0 = _grid_at(r.t_in)
                t1 = GRID[min(GRID.index(t0) + 4, len(GRID) - 1)] if rule == "I3_surge_15m" else "15:15"
                if np.isfinite(x[t0]) and np.isfinite(x[t1]) and x[t0] > 0:
                    rows.append({"d": r.d, "out": r.side * (x[t1] / x[t0] - 1) - LEG})
            sims.append(S.replication(monthly(pd.DataFrame(rows)))["stat"])
    else:
        daily = {s: sc["daily"] for s, sc in scans.items() if len(sc["daily"])}
        C = pd.DataFrame({s: D["c"] for s, D in daily.items()})
        ew = ew_returns(daily, fno)
        cum = (1 + ew.fillna(0)).cumprod()
        live = {d: [s for s in C.columns if d in fno.get(s, ())] for d in pd.unique(ev["d"])}
        for _ in range(draws):
            rows = []
            for r in ev.itertuples():
                cand = live.get(r.d) or []
                if not cand:
                    continue
                s = cand[rng.integers(len(cand))]
                a, b = C.at[r.d, s] if r.d in C.index else np.nan, C.at[r.d_out, s] if r.d_out in C.index else np.nan
                if np.isfinite(a) and np.isfinite(b) and a > 0:
                    bench = cum.get(r.d_out, np.nan) / cum.get(r.d, np.nan) - 1
                    rows.append({"d": r.d, "out": r.side * (b / a - 1 - bench) - SWING_COST})
            sims.append(S.replication(monthly(pd.DataFrame(rows)))["stat"])
    sims = np.array([x for x in sims if np.isfinite(x)])
    q = float(np.quantile(sims, S.NULL_QUANTILE)) if len(sims) else float("nan")
    return {"stat": real, "null_q95": q, "null_mean": float(sims.mean()) if len(sims) else float("nan"),
            "draws": int(len(sims)), "pass": bool(np.isfinite(real) and np.isfinite(q) and real > q)}
