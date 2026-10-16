"""Group 3 of the hundred algorithms: spreads and arbitrage, long-short in theory (the short leg as a stock future).

    pairs_distance   #41  Gatev-Goetzmann-Rouwenhorst: the 20 pairs whose normalised prices moved closest over a
                          formation year, traded for the next half-year when they diverge by 2 formation sigmas,
                          closed when they cross
    pairs_coint      #42  Engle-Granger: a hedge ratio by least squares on log prices, an ADF test on the residual
                          (computed here), the 20 most strongly cointegrated pairs traded on the residual's z-score
    pca_statarb      #44  Avellaneda-Lee: daily returns net of 15 principal components, the residual's cumulative
                          path fitted as a mean-reverting process, traded on its s-score
    exchange_gaps    #47  NSE vs BSE closing prices for the same share, against the cost of crossing the gap
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ADF_5PCT_EG = -3.34                   #: Engle-Granger 5% critical value, two variables, constant, no trend


def adf_t(e: np.ndarray) -> float:
    """t-statistic of gamma in  de_t = gamma e_{t-1} + u_t  (with a constant)."""
    de, lag = np.diff(e), e[:-1]
    X = np.column_stack([np.ones(len(lag)), lag])
    beta, *_ = np.linalg.lstsq(X, de, rcond=None)
    resid = de - X @ beta
    s2 = resid @ resid / (len(de) - 2)
    cov = s2 * np.linalg.inv(X.T @ X)
    return float(beta[1] / np.sqrt(cov[1, 1]))


def _trade_spread(z: np.ndarray, entry: float, exit_: float) -> np.ndarray:
    """Position on the spread: -1 when z > entry (short the spread), +1 when z < -entry, flat when |z| < exit."""
    pos, out = 0, np.zeros(len(z))
    for i, x in enumerate(z):
        if not np.isfinite(x):
            out[i] = pos
            continue
        if pos == 0 and x > entry:
            pos = -1
        elif pos == 0 and x < -entry:
            pos = 1
        elif pos != 0 and abs(x) < exit_:
            pos = 0
        out[i] = pos
    return out


def pairs_distance(px: pd.DataFrame, *, formation: int = 250, trading: int = 125, n_pairs: int = 20,
                   entry: float = 2.0, cost: float = 0.004, daily: bool = False) -> pd.DataFrame:
    """Rolling formation / trading windows. Returns one row per trading window: gross and net return of the pairs book."""
    out = []
    for s in range(formation, len(px) - trading, trading):
        f = px.iloc[s - formation:s].dropna(axis=1)
        t = px.iloc[s:s + trading][f.columns].dropna(axis=1)
        f = f[t.columns]
        if f.shape[1] < 10:
            continue
        nf = (f / f.iloc[0]).to_numpy()
        sq = (nf ** 2).sum(0)
        ssd = sq[:, None] + sq[None, :] - 2 * nf.T @ nf
        iu = np.triu_indices_from(ssd, 1)
        best = np.argsort(ssd[iu])[:n_pairs]
        rets = []
        for k in best:
            i, j = iu[0][k], iu[1][k]
            sd = np.std(nf[:, i] - nf[:, j])
            ti, tj = t.iloc[:, i].to_numpy(), t.iloc[:, j].to_numpy()
            spread = (ti / f.iloc[0, i]) - (tj / f.iloc[0, j])
            pos = _trade_spread(spread / sd, entry, 0.0 + 1e-9)
            ri, rj = np.diff(ti) / ti[:-1], np.diff(tj) / tj[:-1]
            pnl = pos[:-1] * (ri - rj) / 2                            # long one leg, short the other, half each
            chg = np.abs(np.diff(np.concatenate([[0], pos])))
            trades = chg.sum()
            rets.append((pnl.sum(), pnl.sum() - trades * cost / 2))
            if daily:
                day_net = pnl - chg[:-1] * cost / 2
                for k_, dv in enumerate(day_net):
                    out.append({"d": t.index[k_ + 1], "net_day": dv / n_pairs})
        g, n = np.mean([r[0] for r in rets]), np.mean([r[1] for r in rets])
        if not daily:
            out.append({"start": px.index[s], "gross": g, "net": n, "pairs": len(rets)})
    if daily:
        return pd.DataFrame(out).groupby("d")["net_day"].sum().reset_index()
    return pd.DataFrame(out)


def pairs_coint(px: pd.DataFrame, *, formation: int = 250, trading: int = 125, n_pairs: int = 20, candidates: int = 400,
                entry: float = 2.0, exit_: float = 0.5, cost: float = 0.004) -> pd.DataFrame:
    """Candidates = the closest pairs by distance (cointegration tests on every pair would be ~16,000 regressions per
    window); among them the most cointegrated by the Engle-Granger ADF t."""
    out = []
    for s in range(formation, len(px) - trading, trading):
        f = px.iloc[s - formation:s].dropna(axis=1)
        t = px.iloc[s:s + trading][f.columns].dropna(axis=1)
        f = f[t.columns]
        if f.shape[1] < 10:
            continue
        lf = np.log(f.to_numpy())
        nf = (f / f.iloc[0]).to_numpy()
        sq = (nf ** 2).sum(0)
        ssd = sq[:, None] + sq[None, :] - 2 * nf.T @ nf
        iu = np.triu_indices_from(ssd, 1)
        cand = np.argsort(ssd[iu])[:candidates]
        scored = []
        for k in cand:
            i, j = iu[0][k], iu[1][k]
            X = np.column_stack([np.ones(formation), lf[:, j]])
            b, *_ = np.linalg.lstsq(X, lf[:, i], rcond=None)
            e = lf[:, i] - X @ b
            tstat = adf_t(e)
            if tstat < ADF_5PCT_EG:
                scored.append((tstat, i, j, b, e.mean(), e.std()))
        scored.sort()
        rets = []
        lt = np.log(t.to_numpy())
        for tstat, i, j, b, mu, sd in scored[:n_pairs]:  # noqa: B007
            e = lt[:, i] - (b[0] + b[1] * lt[:, j])
            pos = _trade_spread((e - mu) / sd, entry, exit_)
            ri, rj = np.diff(np.exp(lt[:, i])) / np.exp(lt[:-1, i]), np.diff(np.exp(lt[:, j])) / np.exp(lt[:-1, j])
            pnl = pos[:-1] * (ri - b[1] * rj) / (1 + abs(b[1]))
            trades = np.abs(np.diff(np.concatenate([[0], pos]))).sum()
            rets.append((pnl.sum(), pnl.sum() - trades * cost / 2))
        if rets:
            out.append({"start": px.index[s], "gross": np.mean([r[0] for r in rets]),
                        "net": np.mean([r[1] for r in rets]), "pairs": len(rets)})
    return pd.DataFrame(out)


def pca_statarb(rets: pd.DataFrame, *, window: int = 250, k: int = 15, ou_window: int = 60, entry: float = 1.25,
                exit_: float = 0.5, cost: float = 0.002, step: int = 1) -> pd.DataFrame:
    """Each day: PCA on the last ``window`` days of standardised returns, residual returns of each name net of ``k``
    factors, the residual's cumulative path over ``ou_window`` days fitted as AR(1), the s-score; positions from the
    s-score bands; the next day's residual return earned, equal-weighted across open positions."""
    R = rets.to_numpy()
    names = rets.columns
    pos = np.zeros(len(names))
    rows = []
    for d in range(window, len(R) - 1, step):
        W = R[d - window:d]
        ok = np.isfinite(W).all(0) & np.isfinite(R[d + 1])
        if ok.sum() < k + 10:
            continue
        X = W[:, ok]
        mu, sd = X.mean(0), X.std(0) + 1e-12
        Z = (X - mu) / sd
        U, S_, Vt = np.linalg.svd(Z, full_matrices=False)
        F = Z @ Vt[:k].T                                      # factor returns
        B, *_ = np.linalg.lstsq(F, X, rcond=None)
        resid = X - F @ B
        cum = np.cumsum(resid[-ou_window:], axis=0)
        x0, x1 = cum[:-1], cum[1:]                             # AR(1) per name, all at once
        m0, m1 = x0.mean(0), x1.mean(0)
        b = ((x0 - m0) * (x1 - m1)).sum(0) / (((x0 - m0) ** 2).sum(0) + 1e-18)
        a = m1 - b * m0
        good = (b > 0) & (b < 1)
        var_eq = np.where(good, (x1 - (a + b * x0)).var(0) / np.where(good, 1 - b * b, 1), 0.0)
        mean_eq = np.where(good, a / np.where(good, 1 - b, 1), 0.0)
        s = np.where(good & (var_eq > 0), (cum[-1] - mean_eq) / np.sqrt(np.where(var_eq > 0, var_eq, 1)), 0.0)
        p = pos[ok]
        p = np.where(s > entry, -1, np.where(s < -entry, 1, np.where(np.abs(s) < exit_, 0, p)))
        trades = np.abs(p - pos[ok]).sum()
        pos[ok] = p
        nxt = R[d + 1, ok]
        f_next = ((nxt - mu) / sd) @ Vt[:k].T
        r_next = nxt - f_next @ B                              # tomorrow's residual return, from today's model
        n_open = max(np.abs(p).sum(), 1)
        gross = float((p * r_next).sum() / n_open)
        rows.append({"d": rets.index[d + 1], "gross": gross, "net": gross - trades * cost / (2 * n_open),
                     "open": int(np.abs(p).sum())})
    return pd.DataFrame(rows)


def exchange_gaps(con, *, min_adv: float = 1e7, cost: float = 0.0025) -> dict:
    """NSE vs BSE closes for the same ISIN and day, among liquid names: how often the gap exceeds ``cost`` (selling on
    one exchange and buying on the other: two delivery legs, STT on both, charges)."""
    df = con.execute("""
        WITH n AS (SELECT isin, business_date, close_price FROM eod_prices WHERE exchange = 'NSE' AND series = 'EQ'),
             b AS (SELECT isin, business_date, close_price FROM eod_prices WHERE exchange = 'BSE'),
             liq AS (SELECT p.isin, f.business_date FROM features f JOIN adjusted_prices p
                     ON p.lineage = f.lineage AND p.business_date = f.business_date WHERE f.adv20 >= ?)
        SELECT ABS(n.close_price / b.close_price - 1) AS gap FROM n JOIN b USING (isin, business_date)
        JOIN liq USING (isin, business_date) WHERE b.close_price > 0 AND n.close_price > 0""", [min_adv]).df()
    g = df["gap"]
    return {"name_days": int(len(g)), "median_gap_bp": float(g.median() * 1e4), "p99_gap_bp": float(g.quantile(0.99) * 1e4),
            "share_above_cost": float((g > cost).mean())}
