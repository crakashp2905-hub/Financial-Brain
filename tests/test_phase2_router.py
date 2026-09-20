"""Model benchmark scoring and the router: cheapest measured-good model first,
escalate on low confidence, never promote an uncertain answer."""
from __future__ import annotations

import json
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


def test_thresholds_are_per_label_so_a_confident_neutral_cannot_hide_misses():
    """8 neutrals, 2 negatives. The model says neutral to all ten at 0.95: overall
    accuracy among confident answers is 80%, but its 'neutral' is wrong 2 times in 10 -
    so 'neutral' earns no threshold at target 0.9 and everything escalates."""
    golds = ["neutral"] * 8 + ["negative"] * 2
    lazy = [D("neutral", 0.95)] * 10
    s = bench.score(golds, lazy, target=0.9)
    assert s["thresholds"]["neutral"] is None and s["coverage"] == 0
    # A model that is right when confident about each label earns per-label thresholds.
    good = [D("neutral", 0.97)] * 8 + [D("negative", 0.6), D("negative", 0.9)]
    golds2 = ["neutral"] * 8 + ["negative", "negative"]
    s2 = bench.score(golds2, good, target=0.9)
    assert s2["thresholds"]["neutral"] == 0.97
    assert s2["thresholds"]["negative"] is None, "only 2 examples: below MIN_SUPPORT"
    assert s2["coverage"] == pytest.approx(0.8)


def test_uninformative_confidence_earns_no_threshold():
    golds = ["a", "b", "a", "b"]
    ds = [D("a", 1.0), D("a", 1.0), D("a", 1.0), D("b", 1.0)]
    assert bench.score(golds, ds, target=0.9)["threshold"] is None


def _bench_row(con, model, threshold, latency, *, prompt=None):
    """A benchmark row carries the prompt it was measured under; the router only
    considers models measured under the prompt it is about to send (ADR-0003)."""
    detail = json.dumps({"prompt_version": prompt or system1.prompt_version()})
    con.execute("""INSERT INTO model_bench (run_at, task, model, n, accuracy, macro_f1,
                   latency_ms, target, threshold, coverage, detail)
                   VALUES (?, 't', ?, 10, 0.9, 0.9, ?, 0.9, ?, 0.5, ?)""",
                [datetime.now(timezone.utc), model, latency, threshold, detail])


def test_plan_orders_by_tier_then_latency_and_drops_uncalibrated(con, monkeypatch):
    monkeypatch.delenv("FB_LLM_ENABLED", raising=False)
    _bench_row(con, "phi4:latest", 0.8, 9000)
    _bench_row(con, "qwen2.5:3b", 0.9, 1500)
    _bench_row(con, "qwen2.5:7b", None, 3000)          # confidence meaningless
    _bench_row(con, "claude-haiku-4-5", 0.7, 800)      # cloud: off unless enabled
    assert [s["model"] for s in router.plan(con, "t")] == ["qwen2.5:3b", "phi4:latest"]
    monkeypatch.setenv("FB_LLM_ENABLED", "1")
    assert router.plan(con, "t")[-1]["model"] == "claude-haiku-4-5"
    # A model measured under a different prompt is not a candidate at all.
    _bench_row(con, "gemma2:latest", 0.9, 100, prompt="p_old")
    assert "gemma2:latest" not in [s["model"] for s in router.plan(con, "t")]


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


def test_a_route_must_survive_a_set_it_was_not_tuned_on(con, monkeypatch):
    """Thresholds fitted and judged on one set flatter themselves: a route is stored only
    if its accepted answers hold up on a set that had no part in fitting them."""
    import json
    tune = ["neutral"] * 6 + ["negative"] * 2
    held = ["negative"] * 6 + ["neutral"] * 2
    rows = {"t": [{"state": str(i), "gold": g} for i, g in enumerate(tune)],
            "t_holdout": [{"state": str(i), "gold": g} for i, g in enumerate(held)]}
    monkeypatch.setattr(bench, "_rows", lambda task: rows[task])
    # "lazy" is confidently neutral about everything: right on the tuning set, wrong on
    # the held-out one. "careful" is right on both but slower.
    for task, model, items in (
            ("t", "lazy", [["neutral", 0.99, 5]] * 6 + [["negative", 0.99, 5]] * 2),
            ("t_holdout", "lazy", [["neutral", 0.99, 5]] * 8),
            ("t", "careful", [["neutral", 0.95, 500]] * 6 + [["negative", 0.95, 500]] * 2),
            ("t_holdout", "careful", [["negative", 0.95, 500]] * 6
             + [["neutral", 0.95, 500]] * 2)):
        r = bench.score([x["gold"] for x in rows[task]],
                        [system1.Decision(label=a, probs={}, confidence=c, model=model,
                                          latency_ms=ms) for a, c, ms in items], 0.9)
        r["items"] = items
        r["prompt_version"] = system1.prompt_version()
        con.execute("""INSERT INTO model_bench (run_at, task, model, n, threshold,
                       latency_ms, detail) VALUES (NOW(), ?, ?, ?, ?, ?, ?)""",
                    [task, model, len(items), r["threshold"],
                     sum(i[2] for i in items) / len(items), json.dumps(r)])
    unverified = bench.optimise_route(con, "t", budget_ms=10_000)
    assert [s["model"] for s in unverified["chain"]] == ["lazy"], "fastest, looks perfect"
    verified = router.choose(con, "t", budget_ms=10_000, verify_on="t_holdout")
    assert [s["model"] for s in verified["chain"]] == ["careful"]
    assert verified["verified"]["wrong_when_accepted"] == 0
    assert any(r["chain"] == ["lazy"] for r in verified["rejected"])
