"""Pre-registered hypotheses: one in-sample shot, out-of-sample only on unseen data."""
from __future__ import annotations

import pytest

from financial_brain.evaluation import registry
from test_phase2_benchmark import _market, con  # noqa: F401 (fixture)

SPEC = {"name": "momentum 60d", "signal": "ret_60d", "direction": 1, "horizon": 20,
        "positions": 5, "aum_inr": 1e6, "rationale": "trend persistence (planted)"}


def test_register_is_content_addressed(con):  # noqa: F811
    _market(con, drift_by_rank=0.02)
    hid = registry.register(con, SPEC)
    with pytest.raises(registry.RegistryError, match="already registered"):
        registry.register(con, {**SPEC, "name": "same idea, new name"})
    assert hid.startswith("hy_")


def test_in_sample_is_one_shot(con):  # noqa: F811
    _market(con, drift_by_rank=0.02)
    hid = registry.register(con, SPEC)
    r = registry.test(con, hid)
    assert r["verdict"] in ("PROMOTE", "REJECT") and "firewall" in r
    with pytest.raises(registry.RegistryError, match="spent"):
        registry.test(con, hid)


def test_out_of_sample_waits_for_unseen_data_without_spending_a_trial(con):  # noqa: F811
    _market(con, drift_by_rank=0.02)
    hid = registry.register(con, SPEC)
    with pytest.raises(registry.RegistryError, match="out-of-sample rebalances"):
        registry.test(con, hid, mode="out_of_sample")
    assert con.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0] == 0


def test_direction_states_low_is_good(con):  # noqa: F811
    from financial_brain.evaluation import benchmark
    _market(con, drift_by_rank=0.02)
    up = benchmark.evaluate(con, "ret_60d", 20)
    down = benchmark.evaluate(con, "ret_60d", 20, direction=-1)
    assert up["mean_ic"] == pytest.approx(-down["mean_ic"])


def test_bad_specs_are_refused(con):  # noqa: F811
    with pytest.raises(registry.RegistryError):
        registry.register(con, {**SPEC, "signal": "close; DROP TABLE x"})
    with pytest.raises(registry.RegistryError):
        registry.register(con, {k: v for k, v in SPEC.items() if k != "rationale"})
