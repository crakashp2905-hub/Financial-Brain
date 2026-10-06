"""Group 4 of the hundred algorithms: more learners on the deep feature library, in the same walk-forward harness as
the LightGBM challenger (``deep_library.walk_forward``: trained on data ending 30 days before each test year).

    forest      #69's learner - a random forest regressor
    svm         #71 - a linear support-vector regressor on the ranked features
    mlp         #68 - a two-layer neural network
    lstm        #72 - an LSTM over each name's last 20 sampled sessions of the top features
    genetic     #70 - feature weights evolved to maximise the training years' rank IC
    regime      #69 - a classifier choosing each month between the momentum book and the reversal book
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd


def forest():
    from sklearn.ensemble import RandomForestRegressor
    return RandomForestRegressor(n_estimators=150, min_samples_leaf=300, max_features=0.3, n_jobs=4, random_state=0)


def svm():
    from sklearn.svm import LinearSVR
    return LinearSVR(C=0.01, epsilon=0.0, max_iter=5000, random_state=0)


def mlp():
    from sklearn.neural_network import MLPRegressor
    return MLPRegressor(hidden_layer_sizes=(64, 32), alpha=1e-3, learning_rate_init=1e-3, max_iter=30,
                        early_stopping=True, random_state=0)


class Genetic:
    """Weights over the features, evolved to maximise the mean per-date rank IC on the training rows."""

    def __init__(self, pop: int = 30, gens: int = 25, seed: int = 0):
        self.pop, self.gens, self.rng = pop, gens, np.random.default_rng(seed)

    def fit(self, X, y, dates=None):
        n = X.shape[1]
        dates = np.asarray(dates) if dates is not None else np.zeros(len(y))
        groups = [np.flatnonzero(dates == d) for d in np.unique(dates)]
        groups = [g for g in groups if len(g) > 50][:: max(1, len(groups) // 60)]

        def fitness(w):
            s = X @ w
            ics = [np.corrcoef(s[g], y[g])[0, 1] for g in groups]
            return float(np.nanmean(ics))
        P = [self.rng.normal(0, 1, n) for _ in range(self.pop)]
        for _ in range(self.gens):
            scored = sorted(P, key=fitness, reverse=True)
            elite = scored[: self.pop // 4]
            kids = []
            while len(kids) < self.pop - len(elite):
                a, b = elite[self.rng.integers(len(elite))], elite[self.rng.integers(len(elite))]
                mask = self.rng.random(n) < 0.5
                kids.append(np.where(mask, a, b) + self.rng.normal(0, 0.1, n))
            P = elite + kids
        self.w = max(P, key=fitness)
        return self

    def predict(self, X):
        return X @ self.w


def lstm_walk_forward(panel: pd.DataFrame, features: list[str], *, steps: int = 20, years=range(2019, 2027),
                      embargo_days: int = 30, epochs: int = 3, seed: int = 0, min_train: int = 10_000) -> pd.DataFrame:
    """Sequences of each name's last ``steps`` sampled sessions (ranked features); an LSTM trained per test year on
    sequences ending 30 days before it. Returns test rows with ``pred``."""
    import torch
    torch.manual_seed(seed)
    p = panel.sort_values(["lineage", "d"]).reset_index(drop=True)
    R = p.groupby("d")[features].rank(pct=True).fillna(0.5).to_numpy(np.float32)
    y = p.groupby("d")["fwd20"].rank(pct=True).to_numpy(np.float32)
    lin = p["lineage"].to_numpy()
    idx = np.arange(len(p))
    start = np.r_[0, np.flatnonzero(lin[1:] != lin[:-1]) + 1]
    first = np.zeros(len(p), dtype=int)
    for s, e in zip(start, np.r_[start[1:], len(p)]):
        first[s:e] = s
    ok = (idx - first >= steps - 1)
    seq_idx = np.flatnonzero(ok)

    def seqs(rows):
        return np.stack([R[r - steps + 1:r + 1] for r in rows])

    class Net(torch.nn.Module):
        def __init__(self, n):
            super().__init__()
            self.lstm = torch.nn.LSTM(n, 32, batch_first=True)
            self.out = torch.nn.Linear(32, 1)

        def forward(self, x):
            h, _ = self.lstm(x)
            return self.out(h[:, -1]).squeeze(-1)

    d = p["d"].to_numpy()
    preds = pd.Series(np.nan, index=p.index)
    for yr in years:
        st = date(yr, 1, 1)
        tr = seq_idx[(d[seq_idx] < st - timedelta(days=embargo_days)) & np.isfinite(y[seq_idx])]
        te = seq_idx[(d[seq_idx] >= st) & (d[seq_idx] < date(yr + 1, 1, 1))]
        if len(tr) < min_train or len(te) == 0:
            continue
        net = Net(len(features))
        opt = torch.optim.Adam(net.parameters(), lr=1e-3)
        rng = np.random.default_rng(seed + yr)
        for _ in range(epochs):
            for b in np.array_split(rng.permutation(tr), max(len(tr) // 2048, 1)):
                xb, yb = torch.from_numpy(seqs(b)), torch.from_numpy(y[b])
                opt.zero_grad()
                loss = torch.nn.functional.mse_loss(net(xb), yb)
                loss.backward()
                opt.step()
        with torch.no_grad():
            for b in np.array_split(te, max(len(te) // 4096, 1)):
                preds.iloc[b] = net(torch.from_numpy(seqs(b))).numpy()
    p["pred"] = preds.to_numpy()
    return p[p["pred"].notna()]


def regime_switch(panel: pd.DataFrame, market: pd.DataFrame, *, years=range(2019, 2027), step: int = 4) -> pd.DataFrame:
    """Each month (every ``step``-th sampled date): which of the momentum book (top quintile by 12-1 momentum) and
    the reversal book (bottom quintile by 20-day return) beat the other over the next 20 sessions, predicted by a
    random forest from market features known that day, trained only on earlier months. Returns per month the chosen
    book's top-quintile return and both books'."""
    from sklearn.ensemble import RandomForestClassifier
    dates = sorted(panel["d"].unique())[::step]
    rows = []
    for d in dates:
        x = panel[panel["d"] == d].dropna(subset=["fwd20", "mom_12_1", "roc_20"])
        if len(x) < 100:
            continue
        k = len(x) // 5
        mom = x.nlargest(k, "mom_12_1")["fwd20"].mean() - x["fwd20"].mean()
        rev = x.nsmallest(k, "roc_20")["fwd20"].mean() - x["fwd20"].mean()
        m = market.loc[market.index <= d].iloc[-1] if (market.index <= d).any() else None
        if m is None:
            continue
        rows.append({"d": d, "mom": mom, "rev": rev, **m.to_dict()})
    df = pd.DataFrame(rows)
    feats = list(market.columns)
    df["label"] = (df["mom"] > df["rev"]).astype(int)
    out = []
    for yr in years:
        st = date(yr, 1, 1)
        tr = df[df["d"] < st - timedelta(days=30)]
        te = df[(df["d"] >= st) & (df["d"] < date(yr + 1, 1, 1))]
        if len(tr) < 24 or te.empty or tr["label"].nunique() < 2:
            continue
        rf = RandomForestClassifier(n_estimators=200, min_samples_leaf=5, random_state=0).fit(tr[feats], tr["label"])
        pick = rf.predict(te[feats])
        out.append(te.assign(choice=np.where(pick == 1, "momentum", "reversal"),
                             chosen=np.where(pick == 1, te["mom"], te["rev"])))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()
