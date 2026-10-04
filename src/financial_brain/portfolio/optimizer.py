"""Turn expected returns into weights that survive risk and costs.

    maximise   mu'w  -  (lambda/2) w'Sw  -  c'|w - w_prev|
    subject to sum(w) <= 1,  0 <= w_i <= max_weight           (long-only, cash is allowed)

``S`` is a Ledoit-Wolf shrunk covariance: with a few hundred names and a couple of years of returns the
sample covariance is nearly singular, and an optimiser fed it concentrates in whichever name's noise
looks least correlated. The cost term charges every unit of turnover its own round trip, so a position
is only opened or changed when the expected gain beats the friction of moving there - which is why the
solution is sparse and sticky instead of rebalancing to a theoretically optimal set every month.

The turnover is handled exactly by splitting ``w = w_prev + b - s`` with ``b, s >= 0`` and charging
``c'(b + s)``; at the optimum at most one of the pair is non-zero, so this equals the absolute value
without a non-smooth term.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize
from sklearn.covariance import LedoitWolf


class OptimizerError(ValueError):
    pass


def shrunk_cov(returns: np.ndarray) -> np.ndarray:
    r = np.asarray(returns, dtype=float)
    if r.ndim != 2 or r.shape[0] < 20:
        raise OptimizerError("need a (periods x names) matrix with at least 20 periods")
    return LedoitWolf().fit(r).covariance_


def optimize(mu, cov, *, w_prev=None, cost=None, risk_aversion: float = 5.0,
             max_weight: float = 0.08, budget: float = 1.0) -> dict:
    mu = np.asarray(mu, dtype=float)
    n = len(mu)
    cov = np.asarray(cov, dtype=float)
    if cov.shape != (n, n):
        raise OptimizerError(f"cov is {cov.shape}, expected {(n, n)}")
    if max_weight * n < min(budget, 1.0) * 0.5:
        raise OptimizerError("max_weight too small to hold anything meaningful across these names")
    w0 = np.zeros(n) if w_prev is None else np.asarray(w_prev, dtype=float)
    c = np.zeros(n) if cost is None else np.asarray(cost, dtype=float)

    def unpack(x):
        b, s = x[:n], x[n:]
        return w0 + b - s, b, s

    def obj(x):
        w, b, s = unpack(x)
        return -(mu @ w) + 0.5 * risk_aversion * (w @ cov @ w) + c @ (b + s)

    def grad(x):
        w, b, s = unpack(x)
        g = -mu + risk_aversion * (cov @ w)
        return np.r_[g + c, -g + c]

    cons = [{"type": "ineq", "fun": lambda x: budget - unpack(x)[0].sum(),
             "jac": lambda x: np.r_[-np.ones(n), np.ones(n)]}]
    bounds = [(0.0, max_weight)] * n + [(0.0, 1.0)] * n
    # w >= 0 and w <= max_weight expressed on b and s
    cons += [{"type": "ineq", "fun": lambda x: unpack(x)[0],
              "jac": lambda x: np.hstack([np.eye(n), -np.eye(n)])},
             {"type": "ineq", "fun": lambda x: max_weight - unpack(x)[0],
              "jac": lambda x: np.hstack([-np.eye(n), np.eye(n)])}]
    x0 = np.zeros(2 * n)
    res = minimize(obj, x0, jac=grad, bounds=[(0.0, 1.0)] * (2 * n), constraints=cons,
                   method="SLSQP", options={"maxiter": 400, "ftol": 1e-10})
    w, b, s = unpack(res.x)
    w = np.clip(w, 0.0, max_weight)
    w[w < 1e-5] = 0.0
    turnover = float(np.abs(w - w0).sum())
    return {"weights": w, "converged": bool(res.success), "message": res.message,
            "expected_return": float(mu @ w), "risk": float(np.sqrt(w @ cov @ w)),
            "turnover": turnover, "cost": float(c @ np.abs(w - w0)),
            "net_expected": float(mu @ w - c @ np.abs(w - w0)), "n_positions": int((w > 0).sum()),
            "gross": float(w.sum())}
