"""Execution algorithms on a simulated minute tape: no look-ahead, honest passive fills, sensible costs."""
import numpy as np
import pytest

from financial_brain.execution import algos as X


def day(n=345, drift=0.0, seed=0, vol=2e5):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + drift + rng.normal(0, 0.0008, n))
    o = np.concatenate([[100.0], c[:-1]])
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.0005, n))
    l = np.minimum(o, c) * (1 - rng.uniform(0, 0.0005, n))
    v = rng.uniform(0.5, 1.5, n) * vol
    m = 570 + np.arange(n)
    return X.Day(m=m, o=o, h=h, l=l, c=c, v=v, curve=np.full(n, 1 / n), sigma_d=0.015, hs=0.0004)


def test_every_algorithm_completes_the_order():
    d = day()
    for a in X.library():
        r = X.simulate(d, X.Order(qty=50_000, start=0, end=len(d.c) - 1), a)
        assert np.isfinite(r["is_bp"]) and 0 <= r["passive"] <= 1 and 0 <= r["completion"] <= 1, a.name


def test_a_big_order_costs_more_done_at_once_than_spread_over_the_day():
    d = day()
    big = X.Order(qty=0.10 * d.v.sum(), start=0, end=len(d.c) - 1)
    assert X.simulate(d, big, X.Sweep())["is_bp"] > X.simulate(d, big, X.TWAP())["is_bp"] + 15


def test_a_passive_limit_never_fills_unless_the_market_trades_through_it():
    d = day()
    d.l[:] = d.c * 10                                 # the price never trades down: no passive fill possible
    r = X.simulate(d, X.Order(qty=1000, start=0, end=len(d.c) - 1), X.Peg("bid", 0, "p"))
    assert r["passive"] == 0 and r["completion"] == 1


def test_decisions_use_only_past_bars():
    d = day()
    seen = []

    class Spy(X.Algo):
        name = "spy"

        def decide(self, t, remaining, filled):
            seen.append(self.last(t) == (self.day.c[t - 1] if t > 0 else self.day.o[0]))
            return [("mkt", remaining / self.left(t))]
    X.simulate(d, X.Order(qty=1000, start=0, end=50), Spy())
    assert all(seen)


def test_shortfall_in_a_rising_market_punishes_waiting():
    d = day(drift=0.0003)
    o = X.Order(qty=10_000, start=0, end=len(d.c) - 1)
    assert X.simulate(d, o, X.Immediate())["is_bp"] < X.simulate(d, o, X.MarketOnClose())["is_bp"]


def test_the_learned_algorithms_train_on_past_orders_and_run():
    from financial_brain.execution import exec_study as ES
    train = []
    for s in range(30):
        d = day(seed=s)
        d.features["adv_shares"] = d.v.sum()
        train.append((d, X.Order(qty=0.02 * d.v.sum(), start=0, end=len(d.c) - 1)))
    q = ES.QLearner().fit(train, epochs=1)
    g = ES.evolve(train, pop=4, gens=2, sample=10)
    bp = ES.BayesianPegger(ES.prior_from([d for d, _ in train[:5]]))
    test = day(seed=99)
    test.features["adv_shares"] = test.v.sum()
    o = X.Order(qty=0.02 * test.v.sum(), start=0, end=len(test.c) - 1)
    for a in (q, g, bp):
        r = X.simulate(test, o, a)
        assert np.isfinite(r["is_bp"]) and r["completion"] <= 1
