"""Meta-labelling refuses thin evidence, ignores noise, and finds a planted losing context out of sample."""
import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import metalabel as ML


def _trades(n_days=120, per_day=6, planted=0.0, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for d in pd.bdate_range("2026-01-01", periods=n_days):
        for _ in range(per_day):
            gap = rng.choice(["gap_up", "flat", "gap_down"])
            minute = rng.choice(["open", "mid", "late"])
            r = rng.normal(5, 40) - (planted if gap == "gap_down" else 0.0)
            rows.append({"day": d, "gap": gap, "minute": minute, "net_bp": r})
    return pd.DataFrame(rows)


def test_refuses_to_fit_on_thin_evidence():
    with pytest.raises(ML.NotEnoughEvidence):
        ML.fit(_trades(n_days=20), ["gap", "minute"])


def test_noise_flags_nothing():
    r = ML.fit(_trades(seed=1), ["gap", "minute"])
    assert r["flagged"] == [] and r["walk_forward"]["verdict"] == "NO_EVIDENCE"


def test_a_planted_losing_context_is_flagged_and_skipping_it_helps_out_of_sample():
    r = ML.fit(_trades(planted=40.0, seed=2), ["gap", "minute"])
    assert [(g["context"], g["value"]) for g in r["flagged"]] == [("gap", "gap_down")]
    assert r["walk_forward"]["improvement_mean"] > 0


def test_bh_controls_the_false_discovery_rate():
    p = np.array([0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216])
    assert ML.bh(p, 0.05).sum() == 2
