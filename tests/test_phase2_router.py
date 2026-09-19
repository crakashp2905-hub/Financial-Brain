"""Model benchmark scoring and the router: cheapest measured-good model first,
escalate on low confidence, never promote an uncertain answer."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from financial_brain.config import Config
from financial_brain.evaluation import models as bench
from financial_brain.llm import router, system1
from financial_brain.storage.db import Database


@pytest.fixture
def con(tmp_path):
    d = Database(Config(data_root=tmp_path).ensure())
    d.migrate()
    with d.connect() as c:
        yield c


def D(label, conf, model="m"):
    return system1.Decision(label=label, probs={}, confidence=conf, model=model)


def test_threshold_is_where_confident_answers_are_right_enough():
    golds = ["a", "a", "b", "b", "a"]
    ds = [D("a", 0.99), D("a", 0.95), D("a", 0.60), D("b", 0.90), D("b", 0.55)]
    s = bench.score(golds, ds, target=0.9)
    assert s["accuracy"] == pytest.approx(0.6)
    assert s["threshold"] == 0.9 and s["coverage"] == pytest.approx(0.6)


def test_uninformative_confidence_earns_no_threshold():
    golds = ["a", "b", "a", "b"]
    ds = [D("a", 1.0), D("a", 1.0), D("a", 1.0), D("b", 1.0)]
    assert bench.score(golds, ds, target=0.9)["threshold"] is None


def _bench_row(con, model, threshold, latency):
    con.execute("""INSERT INTO model_bench (run_at, task, model, n, accuracy, macro_f1,
                   latency_ms, target, threshold, coverage, detail)
                   VALUES (?, 't', ?, 10, 0.9, 0.9, ?, 0.9, ?, 0.5, '{}')""",
                [datetime.now(timezone.utc), model, latency, threshold])


def test_plan_orders_by_tier_then_latency_and_drops_uncalibrated(con, monkeypatch):
    monkeypatch.delenv("FB_LLM_ENABLED", raising=False)
    _bench_row(con, "phi4:latest", 0.8, 9000)
    _bench_row(con, "qwen2.5:3b", 0.9, 1500)
    _bench_row(con, "qwen2.5:7b", None, 3000)          # confidence meaningless
    _bench_row(con, "claude-haiku-4-5", 0.7, 800)      # cloud: off unless enabled
    assert [s["model"] for s in router.plan(con, "t")] == ["qwen2.5:3b", "phi4:latest"]
    monkeypatch.setenv("FB_LLM_ENABLED", "1")
    assert router.plan(con, "t")[-1]["model"] == "claude-haiku-4-5"


def test_router_escalates_and_marks_uncertain(con, monkeypatch):
    answers = {"small": D("neutral", 0.6, "small"), "big": D("negative", 0.95, "big")}
    monkeypatch.setattr(bench, "decider", lambda task, m: (lambda row: answers[m]))
    steps = [{"model": "small", "threshold": 0.9, "tier": 1},
             {"model": "big", "threshold": 0.9, "tier": 2}]
    r = router.decide(con, "t", {"state": "x"}, steps=steps)
    assert r.accepted and r.decision.label == "negative"
    assert [s["accepted"] for s in r.route] == [False, True]
    assert con.execute("SELECT COUNT(*), MIN(tier) FROM llm_calls").fetchone() == (2, 1)
    answers["big"] = D("negative", 0.5, "big")
    r = router.decide(con, "t", {"state": "x"}, steps=steps)
    assert not r.accepted, "an answer below every bar stays uncertain"


def test_cascade_is_replayed_from_stored_items(con, monkeypatch):
    import json
    golds = ["a", "b", "a"]
    monkeypatch.setattr(bench, "_rows", lambda task: [{"state": str(i), "gold": g}
                                                       for i, g in enumerate(golds)])
    for model, items in (("small", [["a", 0.99, 10], ["a", 0.5, 10], ["b", 0.4, 10]]),
                         ("big", [["a", 0.9, 100], ["b", 0.95, 100], ["a", 0.95, 100]])):
        con.execute("""INSERT INTO model_bench (run_at, task, model, n, detail)
                       VALUES (NOW(), 't', ?, 3, ?)""", [model, json.dumps({"items": items})])
    r = bench.simulate_route(con, "t", [{"model": "small", "threshold": 0.9},
                                        {"model": "big", "threshold": 0.9}])
    assert r["accuracy"] == 1.0 and r["uncertain"] == 0
    assert r["answered_by"] == pytest.approx({"small": 1 / 3, "big": 2 / 3})
    assert r["latency_ms"] == pytest.approx((10 + 110 + 110) / 3)
