"""Alpha Validation Firewall: noise is rejected, a real planted signal passes, and
re-running trials raises the bar."""
from __future__ import annotations

import pytest

from financial_brain.evaluation import firewall
from test_phase2_benchmark import _market, con  # noqa: F401 (fixture)


def test_noise_is_rejected_with_reasons(con):  # noqa: F811
    _market(con, drift_by_rank=0.0)
    r = firewall.validate(con, "ret_60d", 20)
    assert r["verdict"] == "REJECT" and r["reasons"]
    assert not r["gates"]["significance"]


def test_a_strong_signal_clears_the_statistical_gates(con):  # noqa: F811
    _market(con, drift_by_rank=0.02)
    r = firewall.validate(con, "ret_60d", 20)
    assert r["gates"]["significance"] and r["gates"]["walk_forward"] and r["gates"]["costs"]
    # 40 synthetic names: capacity rightly refuses to promote it.
    assert not r["gates"]["capacity"] and r["verdict"] == "REJECT"


def test_every_run_is_a_trial_and_the_bar_rises(con):  # noqa: F811
    _market(con, drift_by_rank=0.0)
    first = firewall.validate(con, "ret_20d", 20)
    for f in ("ret_5d", "ret_60d", "vol_20"):
        firewall.validate(con, f, 20)
    last = firewall.validate(con, "ret_20d", 20)
    assert (first["trials"], last["trials"]) == (1, 5)
    assert con.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0] == 5


def test_expected_max_sharpe_grows_with_trials():
    assert firewall.expected_max_sharpe(1, 0.01) == 0
    assert 0 < firewall.expected_max_sharpe(10, 0.01) < firewall.expected_max_sharpe(1000, 0.01)
