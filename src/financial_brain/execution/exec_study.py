"""Group 1 of the hundred algorithms: the learned execution algorithms and the study that compares them all.

Learned ones fit on training orders only (sessions before ``SPLIT``) and are scored on later sessions:

    BayesianPegger   (#75)  an online Beta posterior of each limit offset's fill rate, from the bars already seen
    KMeansSelector   (#74)  liquidity clusters of names; the best fixed algorithm per cluster on the training orders
    ForestSelector   (#69)  a random forest choosing the algorithm per order from what is known at its start
    evolve           (#70)  a genetic search over the parametric schedule (front-loading, POV cap, passive share)
    QLearner         (#67)  tabular Q-learning over (time left, inventory left, recent move) with rate multipliers
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from . import algos as X

SPLIT = date(2025, 9, 1)
OFFSETS = (-1, 0, 1, 2, 3)            #: ticks below the last price for the Bayesian pegger


class BayesianPegger(X.Algo):
    """Each minute, for each offset, would a 1-share limit there have filled in the minutes already seen? A Beta
    posterior of that per-minute fill rate, seeded by the training prior; choose the offset with the best expected
    saving given the minutes left, against taking liquidity now."""
    name = "bayes_pegger"

    def __init__(self, prior: dict | None = None):
        self.prior = prior or {k: (1.0, 1.0) for k in OFFSETS}

    def reset(self, day, order, rng):
        super().reset(day, order, rng)
        self.post = {k: list(v) for k, v in self.prior.items()}

    def decide(self, t, remaining, filled):
        if t > self.order.start:                       # learn from the minute that just closed
            last_px = self.day.c[t - 2] if t >= 2 else self.day.o[0]
            for k in OFFSETS:
                hit = self.day.l[t - 1] <= last_px - k * X.TICK - X.TICK
                self.post[k][0 if hit else 1] += 1
        left = self.left(t)
        best, best_v = None, 0.0
        for k in OFFSETS:
            a, b = self.post[k]
            p_end = 1 - (1 - a / (a + b)) ** left
            saving = self.day.hs + k * X.TICK / max(self.last(t), 1e-9)
            v = p_end * saving - (1 - p_end) * self.day.hs * 2
            if v > best_v:
                best, best_v = k, v
        if best is None or left <= 5:
            return [("mkt", remaining / left)]
        return [("lmt", remaining, self.last(t) - best * X.TICK)]


def prior_from(train_days: list[X.Day]) -> dict:
    out = {k: [1.0, 1.0] for k in OFFSETS}
    for d in train_days:
        for t in range(1, len(d.c)):
            for k in OFFSETS:
                hit = d.l[t] <= d.c[t - 1] - k * X.TICK - X.TICK
                out[k][0 if hit else 1] += 1
    scale = 50.0 / max(sum(out[OFFSETS[0]]), 1)       # a weak prior: worth 50 minutes of evidence
    return {k: (max(v[0] * scale, 0.5), max(v[1] * scale, 0.5)) for k, v in out.items()}


def order_features(d: X.Day, o: X.Order) -> list[float]:
    s = o.start
    pre = d.c[s - 1] / d.o[0] - 1 if s > 0 else 0.0
    return [o.qty / max(d.features.get("adv_shares", 1.0), 1.0), d.sigma_d, d.hs, pre,
            np.log(max(d.features.get("adv_shares", 1.0), 1.0))]


class Chooser(X.Algo):
    """Delegates each order to an algorithm picked by ``pick(day, order)``."""

    def __init__(self, name: str, pick, by_name: dict):
        self.name, self.pick, self.by_name = name, pick, by_name

    def reset(self, day, order, rng):
        self.inner = self.by_name[self.pick(day, order)]
        self.inner.reset(day, order, rng)

    def decide(self, t, remaining, filled):
        return self.inner.decide(t, remaining, filled)


def kmeans_selector(train: list[tuple], results: pd.DataFrame, by_name: dict, k: int = 4) -> Chooser:
    from sklearn.cluster import KMeans
    F = np.array([[np.log(max(d.features.get("adv_shares", 1.0), 1.0)), d.sigma_d, d.hs] for d, _ in train])
    mu, sd = F.mean(0), F.std(0) + 1e-12
    km = KMeans(n_clusters=k, n_init=10, random_state=0).fit((F - mu) / sd)
    results = results.assign(cluster=km.labels_[results["i"].to_numpy()])
    best = results.groupby(["cluster", "algo"])["is_bp"].mean().reset_index().sort_values("is_bp").drop_duplicates("cluster")
    choice = dict(zip(best["cluster"], best["algo"]))

    def pick(d, o):
        x = (np.array([np.log(max(d.features.get("adv_shares", 1.0), 1.0)), d.sigma_d, d.hs]) - mu) / sd
        return choice.get(int(km.predict(x[None, :])[0]), "twap")
    return Chooser("kmeans_selector", pick, by_name)


def forest_selector(train: list[tuple], results: pd.DataFrame, by_name: dict, candidates: list[str]) -> Chooser:
    from sklearn.ensemble import RandomForestClassifier
    r = results[results["algo"].isin(candidates)]
    best = r.loc[r.groupby("i")["is_bp"].idxmin()].set_index("i")["algo"]
    X_ = np.array([order_features(*train[i]) for i in best.index])
    rf = RandomForestClassifier(n_estimators=200, min_samples_leaf=20, random_state=0).fit(X_, best.to_numpy())

    def pick(d, o):
        return rf.predict(np.array(order_features(d, o))[None, :])[0]
    return Chooser("forest_selector", pick, by_name)


def evolve(train: list[tuple], *, pop: int = 12, gens: int = 8, sample: int = 200, seed: int = 0) -> X.Parametric:
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(train), size=min(sample, len(train)), replace=False)

    def fitness(g):
        a = X.Parametric(*g)
        return float(np.mean([X.simulate(*train[i], a)["is_bp"] for i in idx]))

    P = [(rng.uniform(0.001, 0.05), rng.uniform(0.02, 0.3), rng.uniform(0, 0.8)) for _ in range(pop)]
    for _ in range(gens):
        scored = sorted(P, key=fitness)
        elite = scored[: pop // 3]
        children = []
        while len(children) < pop - len(elite):
            a, b = elite[rng.integers(len(elite))], elite[rng.integers(len(elite))]
            child = tuple(float(np.clip((x + y) / 2 * rng.lognormal(0, 0.2), lo, hi))
                          for x, y, (lo, hi) in zip(a, b, ((0.0005, 0.08), (0.01, 0.4), (0.0, 0.9))))
            children.append(child)
        P = elite + children
    best = min(P, key=fitness)
    return X.Parametric(*best, name="genetic_optimised")


class QLearner(X.Algo):
    """State: time-left bucket (6), inventory-left bucket (5), last 15-minute move (-1/0/+1). Action: a multiple of
    the TWAP rate. Reward: minus the cost of each slice against arrival; the completion cost at the end."""
    name = "q_learning"
    ACTIONS = (0.0, 0.5, 1.0, 2.0, 4.0)

    def __init__(self):
        self.Q = np.zeros((6, 5, 3, len(self.ACTIONS)))
        self.eps, self.training = 0.1, False

    def state(self, t, remaining):
        frac_t = (t - self.order.start) / max(self.order.end - self.order.start, 1)
        frac_q = remaining / self.order.qty
        mv = self.day.c[t - 1] / self.day.c[max(t - 16, 0)] - 1 if t > 0 else 0.0
        return (min(int(frac_t * 6), 5), min(int(frac_q * 5), 4), 0 if mv < -0.001 else 2 if mv > 0.001 else 1)

    def decide(self, t, remaining, filled):
        s = self.state(t, remaining)
        a = int(self.rng.integers(len(self.ACTIONS))) if self.training and self.rng.random() < self.eps else int(np.argmax(self.Q[s]))
        self._last = (s, a)
        return [("mkt", remaining / self.left(t) * self.ACTIONS[a])]

    def fit(self, train: list[tuple], epochs: int = 3, lr: float = 0.1, seed: int = 0):
        self.training = True
        rng = np.random.default_rng(seed)
        for _ in range(epochs):
            for i in rng.permutation(len(train)):
                d, o = train[i]
                self.reset(d, o, rng)
                remaining, arrival = o.qty, d.o[o.start]
                for t in range(o.start, o.end + 1):
                    if remaining <= 1e-9:
                        break
                    q = min(self.decide(t, remaining, o.qty - remaining)[0][1], remaining, X.MAX_PART * d.v[t])
                    px = X._mkt_price(d, t, q) if q > 0 else 0.0
                    remaining -= q
                    r = -(q * (px / arrival - 1)) / o.qty * 1e4 if q > 0 else 0.0
                    if t == o.end and remaining > 0:
                        r -= remaining * (X._mkt_price(d, t, remaining) / arrival - 1) / o.qty * 1e4
                    s, a = self._last
                    s2 = self.state(min(t + 1, o.end), max(remaining, 0))
                    self.Q[s][a] += lr * (r + self.Q[s2].max() - self.Q[s][a])
        self.training = False
        return self


def load_days(con, symbols: list[str], *, start=None, end=None) -> dict:
    """{(symbol, date): Day} with each day's curve, volatility and ADV from the previous 20 sessions only."""
    out = {}
    for sym in symbols:
        df = con.execute("""SELECT CAST(ts AT TIME ZONE 'Asia/Kolkata' AS DATE) d,
                                   hour(ts AT TIME ZONE 'Asia/Kolkata') * 60 + minute(ts AT TIME ZONE 'Asia/Kolkata') m,
                                   open o, high h, low l, close c, volume v
                            FROM minute_bars WHERE tradingsymbol = ? ORDER BY ts""", [sym]).df()
        df = df[(df["m"] >= 555) & (df["m"] <= 929)]
        days = [(pd.Timestamp(d).date(), g) for d, g in df.groupby("d") if len(g) >= 300]
        if len(days) < 25:
            continue
        grid = np.arange(555, 930)
        vols = []
        closes = []
        for k, (d, g) in enumerate(days):
            vv = pd.Series(g["v"].to_numpy(float), index=g["m"].to_numpy()).reindex(grid).fillna(0).to_numpy()
            if k >= 20:
                hist = np.array(vols[-20:])
                curve = hist.sum(0) / max(hist.sum(), 1.0)
                rets = np.diff(np.log(closes[-21:]))
                sigma = float(np.std(rets, ddof=1)) if len(rets) > 5 else 0.02
                adv = float(hist.sum(1).mean())
                gg = g.set_index("m").reindex(grid).ffill().bfill()
                out[(sym, d)] = X.Day(m=grid.copy(), o=gg["o"].to_numpy(float), h=gg["h"].to_numpy(float),
                                      l=gg["l"].to_numpy(float), c=gg["c"].to_numpy(float), v=vv, curve=curve,
                                      sigma_d=max(sigma, 0.003), hs=0.0, features={"adv_shares": adv})
            vols.append(vv)
            closes.append(float(g["c"].iloc[-1]))
    return out
