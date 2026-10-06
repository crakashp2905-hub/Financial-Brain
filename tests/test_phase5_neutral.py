"""Neutralised IC: a signal that is only a beta bet loses its IC once beta is controlled; a real one keeps it."""
import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import neutral as N


def panel(n_sessions=30, n=300, seed=1, pure_beta=False):
    rng = np.random.default_rng(seed)
    rows = []
    for s in range(n_sessions):
        beta = rng.normal(1, 0.3, n)
        sector = rng.choice(list("ABCDE"), n)
        skill = rng.normal(0, 1, n)
        mkt = rng.normal(0.03, 0.01)                 # a rising market: when a beta bet looks like skill
        sig = beta + rng.normal(0, 0.3, n) if pure_beta else skill
        y = beta * mkt + (0 if pure_beta else 0.02 * skill) + rng.normal(0, 0.05, n)
        rows.append(pd.DataFrame({"session": s, "beta": beta, "sector": sector, "size": rng.normal(0, 1, n),
                                  "vol": rng.normal(0.3, 0.05, n), "sig": sig, "fwd20": y}))
    return pd.concat(rows, ignore_index=True)


def ic(p, controls):
    return np.nanmean([N.residual_ic(g, "sig", 1, "fwd20", controls) for _, g in p.groupby("session")])


def test_a_real_stock_selection_signal_survives_neutralisation():
    p = panel()
    assert ic(p, ("beta", "sector", "size")) == pytest.approx(ic(p, ()), abs=0.05)
    assert ic(p, ("beta", "sector", "size")) > 0.2


def test_a_disguised_beta_bet_loses_its_ic_once_beta_is_controlled():
    p = panel(pure_beta=True)
    assert abs(ic(p, ())) > 0.1                      # looks like a signal every session the market moves
    assert abs(ic(p, ("beta",))) < 0.05


def test_too_few_names_gives_no_number():
    p = panel(n=50)
    assert np.isnan(N.residual_ic(p[p["session"] == 0], "sig", 1, "fwd20", ("beta",)))
