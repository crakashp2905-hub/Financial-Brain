from __future__ import annotations

import pytest

from financial_brain.kernel.registry import ModelRegistry, RegistryError, StrategyRegistry, StrategySpec
from financial_brain.kernel.research import (
    capsule,
    confidence_status,
    effective_sample_size,
    holm_adjust,
    placebo_outperforms,
    reversed_signal,
)


def _spec(**kw: object) -> StrategySpec:
    base = dict(strategy_id="orb", version="1.0", params={"range_bars": 15, "rr": 2}, owner="owner")
    base.update(kw)
    return StrategySpec(**base)  # type: ignore[arg-type]


def test_spec_hash_stable_and_order_independent() -> None:
    a = StrategySpec("orb", "1.0", {"range_bars": 15, "rr": 2}, "owner")
    b = StrategySpec("orb", "1.0", {"rr": 2, "range_bars": 15}, "owner")
    assert a.hash() == b.hash()
    assert StrategySpec.from_json(a.to_json()) == a


def test_registry_refuses_overwrite_but_allows_identical_reregistration() -> None:
    reg = StrategyRegistry()
    h = reg.register(_spec())
    assert reg.register(_spec()) == h
    with pytest.raises(RegistryError, match="immutable"):
        reg.register(_spec(params={"range_bars": 30, "rr": 2}))


def test_model_promotion_requires_shadow_then_paper_with_gates() -> None:
    models = ModelRegistry()
    models.propose("m1", b"weights-v1")
    with pytest.raises(RegistryError, match="gates not passed"):
        models.move("m1", "SHADOW", gates_passed=False, reason="try")
    models.move("m1", "SHADOW", gates_passed=True, reason="gates ok")
    with pytest.raises(RegistryError, match="gates not passed"):
        models.move("m1", "PAPER", gates_passed=False, reason="try")
    rec = models.move("m1", "PAPER", gates_passed=True, reason="shadow ok")
    assert rec.state == "PAPER" and len(rec.history) == 2


def test_modified_weights_refused_under_old_hash() -> None:
    models = ModelRegistry()
    models.propose("m1", b"weights-v1")
    models.load("m1", b"weights-v1")
    with pytest.raises(RegistryError, match="hash mismatch"):
        models.load("m1", b"weights-v1-tampered")


def test_suspended_model_cannot_score() -> None:
    models = ModelRegistry()
    models.propose("m1", b"w")
    models.move("m1", "SUSPENDED", gates_passed=False, reason="drawdown")
    with pytest.raises(RegistryError, match="suspended"):
        models.load("m1", b"w")


def test_holm_adjusts_monotonically() -> None:
    adj = holm_adjust([0.01, 0.04, 0.03])
    assert adj[0] == pytest.approx(0.03)
    assert adj[2] == pytest.approx(0.06)
    assert all(0 <= p <= 1 for p in adj)


def test_placebo_that_matches_strategy_is_flagged() -> None:
    assert placebo_outperforms(1.0, [0.2, 1.5, 1.2, 0.1]) is True
    assert placebo_outperforms(3.0, [0.2, 0.5, 0.1, 0.4]) is False


def test_reversed_signal_is_negation() -> None:
    assert reversed_signal([1, -1, 0]) == [-1, 1, 0]


def test_overlapping_events_do_not_inflate_sample_size() -> None:
    assert effective_sample_size([0, 1, 2, 3, 10], horizon=5) == 2


def test_capsule_digest_changes_with_any_input() -> None:
    a = capsule(code_sha="abc", data_sha256="d1", params={"x": 1}, result={"sharpe": 0.1})
    b = capsule(code_sha="abc", data_sha256="d1", params={"x": 2}, result={"sharpe": 0.1})
    assert a["digest"] != b["digest"]
    assert capsule(code_sha="abc", data_sha256="d1", params={"x": 1}, result={"sharpe": 0.1})["digest"] == a["digest"]


def test_small_sample_reports_insufficient_evidence() -> None:
    assert confidence_status(5) == "INSUFFICIENT_EVIDENCE"
    assert confidence_status(40) == "SUFFICIENT"
