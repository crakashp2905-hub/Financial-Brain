"""SPA / Reality Check and PBO on planted families: noise looks like noise; one real strategy is found."""
import numpy as np
import pandas as pd

from financial_brain.evaluation import family_validation as FV


def _family(k=12, n=128, real=None, seed=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(0.02 * rng.standard_normal((n, k)), columns=[f"s{i}" for i in range(k)])
    if real is not None:
        X["s0"] += real
    return X


def test_on_noise_pbo_averages_a_half_and_spa_rarely_rejects():
    """One noise family's PBO ranges from ~0.15 to ~0.6 by draw, so calibration is checked across many."""
    pbos, rejects = [], 0
    for seed in range(20):
        X = _family(seed=100 + seed)
        pbos.append(FV.pbo(X, blocks=8)["pbo"])
        rejects += FV.reality_check(X, reps=300, seed=seed)["spa_p"] < 0.05
    assert 0.35 < np.mean(pbos) < 0.65
    assert rejects <= 3                               # about 5% expected


def test_one_real_strategy_is_found():
    X = _family(real=0.012, seed=2)                  # Sharpe ~0.6 a period: unmistakable
    rc = FV.reality_check(X, reps=500)
    assert rc["best"] == "s0" and rc["spa_p"] < 0.05
    assert FV.pbo(X, blocks=8)["pbo"] < 0.1
