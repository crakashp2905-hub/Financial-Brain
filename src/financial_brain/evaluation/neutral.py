"""Does a signal select stocks, or load on beta, sector, size or volatility?

Pre-registered in ``PRE-REGISTERED neutralised signal tests``. On each evaluation session both the signal (as
normal scores of its ranks) and the forward return are residualised on the controls by cross-sectional least
squares, and the statistic is the correlation of the residuals - the part of the signal's ranking that the
controls do not explain, against the part of the return they do not explain. Levels add controls one at a time:

    raw -> beta -> + sector -> + size -> + volatility

All controls are known on the session: beta from the previous 250 sessions, the home sector measured once a year
from the previous 250 sessions (``graph/exposure``), log turnover as the size proxy (no point-in-time market cap
exists for every name), 60-session volatility.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

LEVELS = (("raw", ()), ("beta", ("beta",)), ("+sector", ("beta", "sector")),
          ("+size", ("beta", "sector", "size")), ("+vol", ("beta", "sector", "size", "vol")))
SIGNALS = {"momentum": ("mom_12_1", 1), "52w_high": ("dist_52w_high", 1), "low_vol": ("vol_60", -1),
           "reversal": ("ret_20d", -1), "composite": ("composite", 1)}
MIN_NAMES = 100


class NeutralError(ValueError):
    pass


def _nscore(x: pd.Series) -> np.ndarray:
    r = x.rank(method="average").to_numpy()
    return norm.ppf((r - 0.5) / len(r))


def _design(df: pd.DataFrame, controls: tuple) -> np.ndarray:
    cols = [np.ones(len(df))]
    for c in controls:
        if c == "sector":
            d = pd.get_dummies(df["sector"].fillna("none"), drop_first=True, dtype=float)
            cols += [d[k].to_numpy() for k in d.columns]
        else:
            v = df[c].to_numpy(dtype=float)
            cols.append((v - v.mean()) / (v.std() or 1.0))
    return np.column_stack(cols)


def residual_ic(df: pd.DataFrame, signal: str, sign: int, outcome: str, controls: tuple) -> float:
    """One session: correlation of the signal and outcome after both are regressed on ``controls``."""
    d = df.dropna(subset=[signal, outcome, *[c for c in controls if c != "sector"]])
    if len(d) < MIN_NAMES:
        return float("nan")
    s = sign * _nscore(d[signal])
    y = d[outcome].to_numpy(dtype=float)
    X = _design(d, controls)
    beta_s, *_ = np.linalg.lstsq(X, s, rcond=None)
    beta_y, *_ = np.linalg.lstsq(X, y, rcond=None)
    rs, ry = s - X @ beta_s, y - X @ beta_y
    if rs.std() == 0 or ry.std() == 0:
        return float("nan")
    return float(np.corrcoef(rs, ry)[0, 1])


def table(panel: pd.DataFrame, outcome: str = "fwd20") -> pd.DataFrame:
    """``panel``: one row per (session, name) with the signals, the controls and the outcome."""
    rows = []
    for name, (col, sign) in SIGNALS.items():
        for level, controls in LEVELS:
            if name == "low_vol" and "vol" in controls:
                continue                              # the signal is the control: undefined
            ics = panel.groupby("session").apply(lambda g: residual_ic(g, col, sign, outcome, controls)).dropna()  # noqa: B023
            if len(ics) < 10:
                continue
            rows.append({"signal": name, "level": level, "sessions": len(ics), "ic": float(ics.mean()),
                         "t": float(ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics))))})
    out = pd.DataFrame(rows)
    raw = out[out["level"] == "raw"].set_index("signal")["ic"]
    out["kept"] = out.apply(lambda r: r["ic"] / raw[r["signal"]] if raw.get(r["signal"]) else np.nan, axis=1)
    return out


def build_panel(con, *, start, end, names: int = 500, step: int = 20, sector_every: int = 250) -> pd.DataFrame:
    """Signals, controls and forward returns on every ``step``-th session, all point in time."""
    from ..graph import exposure as X
    from ..opportunity import engine as E
    sessions = [pd.Timestamp(r[0]).date() for r in con.execute("""SELECT DISTINCT business_date FROM features
                 WHERE business_date BETWEEN ? AND ? ORDER BY 1""", [start, end]).fetchall()]
    px = con.execute("""SELECT lineage, business_date, close_adj FROM adjusted_prices
                        WHERE business_date BETWEEN ?::DATE - INTERVAL 420 DAY AND ?::DATE + INTERVAL 120 DAY
                          AND close_adj > 0""", [start, end]).df()
    close = px.pivot_table(index="business_date", columns="lineage", values="close_adj").sort_index()
    rets = close.pct_change(fill_method=None)
    idx = con.execute("""SELECT index_name, business_date, close_level FROM index_levels_canonical
                         WHERE variant = 'PRICE' AND index_name IN (SELECT UNNEST(?))""",
                      [[X.MARKET, *X.SECTORS]]).df().pivot_table(index="business_date", columns="index_name",
                                                                  values="close_level").sort_index()
    fret = idx.pct_change(fill_method=None).reindex(rets.index)
    mkt = fret[X.MARKET]
    fwd20 = close.shift(-20) / close - 1
    fwd60 = close.shift(-60) / close - 1
    pos = {pd.Timestamp(x).date(): i for i, x in enumerate(close.index)}
    sectors, sector_asof = {}, None
    out = []
    for k, d in enumerate(sessions[::step]):  # noqa: B007
        if d not in pos or pos[d] < 260:
            continue
        cs = con.execute("""SELECT lineage, adv20, vol_60, mom_12_1, dist_52w_high, ret_20d FROM features
                            WHERE business_date = ? AND adv20 > 0 AND vol_60 > 0 ORDER BY adv20 DESC LIMIT ?""",
                         [d, names]).df().dropna()
        cs = cs[cs["lineage"].isin(close.columns)]
        i = pos[d]
        win = rets.iloc[i - 249:i + 1][cs["lineage"]]
        m = mkt.iloc[i - 249:i + 1]
        mv = m.var()
        cs["beta"] = [float(win[c].cov(m) / mv) if win[c].notna().sum() >= 120 else np.nan for c in cs["lineage"]]
        if sector_asof is None or (pd.Timestamp(d) - pd.Timestamp(sector_asof)).days >= sector_every * 365 / 252:
            ex, _ = X.build_from_returns(rets.iloc[i - 249:i + 1].dropna(axis=1, thresh=120), fret.iloc[i - 249:i + 1])
            sectors = dict(zip(ex["ticker"], ex["home_sector"]))
            sector_asof = d
        cs["sector"] = cs["lineage"].map(sectors)
        cs["size"] = np.log(cs["adv20"])
        cs["vol"] = cs["vol_60"]
        cs["composite"] = E.composite(cs).to_numpy()
        cs["fwd20"] = fwd20.iloc[i][cs["lineage"]].to_numpy()
        cs["fwd60"] = fwd60.iloc[i][cs["lineage"]].to_numpy()
        cs["session"] = d
        out.append(cs)
    if not out:
        raise NeutralError("no sessions with enough history")
    return pd.concat(out, ignore_index=True)
