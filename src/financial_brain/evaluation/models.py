"""Financial-Brain model benchmark: choose models by measurement, not reputation.

Every candidate - rules, FinBERT, each local Ollama model, later cloud models and Jev -
answers the same labelled tasks through the same typed ``decide`` interface. For each
(task, model) we record:

* accuracy and macro-F1 (macro, because "neutral" dominates and a model that always says
  neutral would otherwise look good);
* mean latency - on *this* machine, which is what the router pays;
* a **calibrated acceptance threshold**: the lowest confidence ``t`` at which the answers
  the model gives with confidence >= t are right at least ``target`` of the time, and
  the share of inputs it can then answer on its own (coverage). A model whose
  confidences carry no information gets no usable threshold, however accurate it is on
  average - the router cannot tell its good answers from its bad ones.

Results go to ``model_bench`` (append-only); the router reads the latest row per pair.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..events.classify import classify
from ..llm import backends, system1

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
MIN_SUPPORT = 3                 # a label's threshold needs at least this many examples

SENTIMENT_CHOICES = ["positive", "negative", "neutral"]
SENTIMENT_NOTES = {"positive": "states a favourable fact for shareholders (order won, "
                               "upgrade, profit growth, dividend)",
                   "negative": "states an adverse fact (auditor resigned, tax demand, "
                               "penalty, default, key executive quit)",
                   "neutral": "routine, procedural, or no stated direction"}


def _rows(task: str) -> list[dict]:
    if task in ("sentiment", "sentiment_holdout", "sentiment_news",
                "sentiment_news_holdout"):
        f = {"sentiment": "sentiment_labels.json",
             "sentiment_holdout": "sentiment_labels_holdout.json",
             "sentiment_news": "sentiment_labels_news.json",
             "sentiment_news_holdout": "sentiment_labels_news_holdout.json"}[task]
        return [{"state": r["headline"], "gold": r["gold"]} for r in
                json.loads((FIXTURES / f).read_text("utf-8"))["rows"]]
    if task == "event_type":
        out = []
        for f in ("announcement_labels.json", "announcement_labels_holdout.json"):
            for r in json.loads((FIXTURES / f).read_text("utf-8"))["rows"]:
                out.append({"state": f"BSE subcategory: {r['subcategory']}\n"
                                     f"Subject: {r['subject']}\nHeadline: {r['headline']}",
                            "gold": r["gold_type"], "raw": r})
        return out
    raise ValueError(f"unknown task {task}")


def base(task: str) -> str:
    """A held-out set is scored as its base task (same choices, prompt, thresholds)."""
    for suffix in ("_holdout", "_news"):
        task = task.removesuffix(suffix)
    return task


def choices(task: str) -> list[str]:
    if base(task) == "sentiment":
        return SENTIMENT_CHOICES
    return sorted({r["gold"] for r in _rows(task)})


INSTRUCTION = {
    "sentiment": "Classify this Indian stock-exchange filing by its likely effect on the "
                 "company's shareholders, judging only from what the text states.",
    "event_type": "Classify this Indian stock-exchange (BSE) announcement into the event "
                  "type that best describes what happened.",
}


def decider(task: str, model: str):
    """A function state -> Decision for this (task, model), or raise if unsupported."""
    ch = choices(task)
    task = base(task) if task != base(task) and model != "rules" else task
    if model == "rules":
        if task != "event_type":
            raise ValueError("rules only exist for event_type")

        def rules(row):
            r = row["raw"]
            kind, _, rule = classify(r["category"], r["subcategory"], r["headline"],
                                     r["subject"])
            conf = 0.5 if rule in ("no rule matched", "subcategory General") else 1.0
            return system1.Decision(label=kind, probs={}, confidence=conf, model="rules")
        return rules
    if model == "finbert":
        if base(task) != "sentiment":
            raise ValueError("finbert only does sentiment")
        from ..config import load
        fb = backends.FinBERT(load().data_root / "models" / "finbert")
        return lambda row: system1.finbert_decide(fb, row["state"])
    from ..llm import router
    if router.registry().get(model, {}).get("system_one", True) is False:
        raise ValueError(f"{model} is reasoning-only (system_one = false); it cannot give "
                         "a one-token typed decision")
    notes = SENTIMENT_NOTES if base(task) == "sentiment" else None
    style = router.registry().get(model, {}).get("answer_style", "letters")
    return lambda row: system1.ollama_decide(model, INSTRUCTION[task], row["state"], ch,
                                             notes=notes, style=style)


def score(golds: list[str], decisions: list, target: float) -> dict:
    # strict=True throughout: a decisions/golds length mismatch means the benchmark ran
    # on a different set than it is being scored against, which must fail loudly rather
    # than silently truncate and report an accuracy for the wrong items.
    labels = sorted(set(golds) | {d.label for d in decisions})
    correct = [d.label == g for d, g in zip(decisions, golds, strict=True)]
    f1s = []
    for lab in set(golds):
        tp = sum(1 for d, g in zip(decisions, golds, strict=True) if d.label == lab == g)
        fp = sum(1 for d, g in zip(decisions, golds, strict=True) if d.label == lab != g)
        fn = sum(1 for d, g in zip(decisions, golds, strict=True) if g == lab != d.label)
        f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    # Per predicted label: the lowest confidence at which that label's confident
    # predictions were right >= target of the time (min MIN_SUPPORT of them). Overall
    # accuracy would let a model that confidently says "neutral" to everything qualify on
    # an imbalanced set while missing every adverse filing.
    thresholds = {}
    for lab in labels:
        preds = [(d.confidence, c) for d, c in zip(decisions, correct, strict=True) if d.label == lab]
        thresholds[lab] = None
        for t in sorted({round(conf, 4) for conf, _ in preds}):
            kept = [c for conf, c in preds if conf >= t]
            if len(kept) >= MIN_SUPPORT and sum(kept) / len(kept) >= target:
                thresholds[lab] = t
                break
    accepted = [d for d in decisions if thresholds.get(d.label) is not None
                and d.confidence >= thresholds[d.label]]
    usable = [t for t in thresholds.values() if t is not None]
    confusion = Counter((g, d.label) for d, g in zip(decisions, golds, strict=True) if d.label != g)
    return {"n": len(golds), "accuracy": sum(correct) / len(golds),
            "macro_f1": sum(f1s) / len(f1s), "thresholds": thresholds,
            "threshold": min(usable) if usable else None,
            "coverage": len(accepted) / len(decisions),
            "target": target, "labels": labels,
            "top_confusions": [f"{g} -> {p} x{n}" for (g, p), n in confusion.most_common(5)]}


def bench(con, task: str, model: str, *, target: float = 0.9, limit: int | None = None,
          record: bool = True) -> dict:
    rows = _rows(task)[:limit] if limit else _rows(task)
    decide = decider(task, model)
    decisions = [decide(r) for r in rows]
    out = score([r["gold"] for r in rows], decisions, target)
    out.update(task=task, model=model, prompt_version=system1.prompt_version(),
               latency_ms=sum(d.latency_ms for d in decisions) / len(decisions),
               # per item, so a router cascade can be simulated without re-running models
               items=[[d.label, round(d.confidence, 5), d.latency_ms] for d in decisions])
    if record:
        con.execute("""INSERT INTO model_bench (run_at, task, model, n, accuracy, macro_f1,
                       latency_ms, target, threshold, coverage, detail)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    [datetime.now(timezone.utc), task, model, out["n"], out["accuracy"],
                     out["macro_f1"], out["latency_ms"], target, out["threshold"],
                     out["coverage"], json.dumps(out)])
    return out


def simulate_route(con, task: str, steps: list[dict], *, items_task: str | None = None) -> dict:
    """End-to-end result of a router cascade, replayed from stored benchmark items:
    accuracy, share answered by each step, share left uncertain, mean latency paid, and
    how often an *accepted* answer was wrong. ``items_task`` replays the same steps (and
    their thresholds) on another set - that is how a route is verified out-of-sample."""
    task = items_task or task
    golds = [r["gold"] for r in _rows(task)]
    items = {}
    for s in steps:
        row = con.execute("""SELECT detail FROM model_bench WHERE task = ? AND model = ?
                             ORDER BY run_at DESC LIMIT 1""", [task, s["model"]]).fetchone()
        stored = json.loads(row[0]).get("items") if row else None
        if not stored or len(stored) != len(golds):
            raise ValueError(f"no per-item benchmark for {s['model']} on {task}; re-run bench")
        items[s["model"]] = stored
    right, paid, answered_by, uncertain = 0, 0.0, Counter(), 0
    accepted, accepted_wrong, per_label = 0, 0, Counter()
    for i, g in enumerate(golds):
        label, was_accepted = None, False
        for s in steps:
            lab, conf, lat = items[s["model"]][i]
            paid += lat
            label = lab
            if accepts(s, lab, conf):
                answered_by[s["model"]] += 1
                was_accepted = True
                break
        else:
            uncertain += 1
        right += label == g
        accepted += was_accepted
        accepted_wrong += was_accepted and label != g
        per_label[(g, label if was_accepted else "uncertain")] += 1
    n = len(golds)
    recall = {g: per_label[(g, g)] / max(1, sum(v for (gg, _), v in per_label.items()
                                                if gg == g)) for g in set(golds)}
    return {"accuracy": right / n, "latency_ms": paid / n, "uncertain": uncertain / n,
            "answered_by": {m: c / n for m, c in answered_by.items()},
            "wrong_when_accepted": accepted_wrong / max(1, accepted),
            "recall_when_accepted": recall, "set": task}


def accepts(step: dict, label: str, confidence: float) -> bool:
    """Does this route step trust this answer? Per-label thresholds when known."""
    per = step.get("thresholds")
    if per is not None:
        t = per.get(label)
        return t is not None and confidence >= t
    return step.get("threshold") is not None and confidence >= step["threshold"]


def rescore(con, task: str, *, target: float = 0.9) -> list[dict]:
    """Recompute thresholds for each model's latest run from its stored items - no model
    calls. Appends new rows marked ``rescored``."""
    golds = [r["gold"] for r in _rows(task)]
    out = []
    for model, detail, lat in con.execute("""
            SELECT model, detail, latency_ms FROM (SELECT *, ROW_NUMBER() OVER (
            PARTITION BY model ORDER BY run_at DESC) rk FROM model_bench WHERE task = ?)
            WHERE rk = 1""", [task]).fetchall():
        items = json.loads(detail or "{}").get("items")
        if not items or len(items) != len(golds):
            continue
        ds = [system1.Decision(label=a, probs={}, confidence=c, model=model, latency_ms=ms)
              for a, c, ms in items]
        r = score(golds, ds, target)
        r.update(task=task, model=model, latency_ms=lat, items=items, rescored=True,
                 prompt_version=json.loads(detail or "{}").get("prompt_version"))
        con.execute("""INSERT INTO model_bench (run_at, task, model, n, accuracy, macro_f1,
                       latency_ms, target, threshold, coverage, detail)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    [datetime.now(timezone.utc), task, model, r["n"], r["accuracy"],
                     r["macro_f1"], lat, target, r["threshold"], r["coverage"],
                     json.dumps(r)])
        out.append(r)
    return out


def optimise_route(con, task: str, *, budget_ms: float = 4000, max_len: int = 3,
                   verify_on: str | None = None, max_accepted_error: float = 0.10) -> dict:
    """Rank chains on ``task``, then **verify the best ones on ``verify_on``** - a set
    whose labels played no part in choosing the thresholds - and return the first whose
    accepted answers are wrong no more than ``max_accepted_error`` of the time.

    Thresholds fitted and judged on one set are optimistic: the first sentiment route
    scored 89% in-sample and 65% held out, accepting a wrong answer 24% of the time.
    A route that cannot be verified is not returned - the router then declines rather
    than answering badly.
    """
    from itertools import combinations

    from ..llm import router
    steps = router.plan(con, task, optimised=False)
    ranked = []
    for k in range(1, max_len + 1):
        for chain in combinations(steps, k):
            try:
                sim = simulate_route(con, task, list(chain))
            except ValueError:
                continue
            if sim["latency_ms"] <= budget_ms:
                ranked.append(((sim["accuracy"] - sim["uncertain"], -sim["latency_ms"]),
                               list(chain), sim))
    ranked.sort(key=lambda r: r[0], reverse=True)
    if not ranked:
        return {"task": task, "chain": [], "note": f"nothing fits {budget_ms:.0f} ms"}
    if not verify_on:
        _, chain, sim = ranked[0]
        return {"task": task, "chain": chain, "simulated": sim, "budget_ms": budget_ms,
                "verified": None, "note": "not verified out-of-sample"}
    rejected = []
    for _, chain, sim in ranked:
        try:
            held = simulate_route(con, task, chain, items_task=verify_on)
        except ValueError as e:
            rejected.append({"chain": [c["model"] for c in chain], "why": str(e)})
            continue
        if held["wrong_when_accepted"] <= max_accepted_error:
            return {"task": task, "chain": chain, "simulated": sim, "verified": held,
                    "budget_ms": budget_ms, "rejected": rejected[:5]}
        rejected.append({"chain": [c["model"] for c in chain],
                         "wrong_when_accepted": round(held["wrong_when_accepted"], 3),
                         "uncertain": round(held["uncertain"], 3)})
    return {"task": task, "chain": [], "rejected": rejected[:8],
            "note": f"no chain kept accepted errors <= {max_accepted_error:.0%} on "
                    f"{verify_on}; the router will decline this task"}


def validate_route(con, route_task: str, holdout: str) -> dict:
    """Run the *stored* route for ``route_task`` - thresholds and chain fixed on the
    tuning set - live on a held-out set, and score it. Nothing is tuned here."""
    from ..llm import router
    steps = router.plan(con, route_task)
    rows = _rows(holdout)
    golds, labels, accepted, lat = [], [], 0, 0
    per = Counter()
    for r in rows:
        routed = router.decide(con, route_task, r, steps=steps)
        golds.append(r["gold"])
        labels.append(routed.decision.label if routed.accepted else "uncertain")
        accepted += routed.accepted
        lat += sum(1 for _ in routed.route)
        per[(r["gold"], labels[-1])] += 1
    n = len(rows)
    recall = {g: sum(v for (gg, p), v in per.items() if gg == g and p == g) /
              max(1, sum(v for (gg, _), v in per.items() if gg == g)) for g in set(golds)}
    wrong_accepted = sum(1 for g, p in zip(golds, labels, strict=True) if p not in ("uncertain", g))
    return {"route": [s["model"] for s in steps], "n": n,
            "accuracy_counting_uncertain_as_wrong": sum(g == p for g, p in zip(golds, labels, strict=True)) / n,
            "uncertain": labels.count("uncertain") / n,
            "wrong_when_accepted": wrong_accepted / max(1, accepted),
            "recall": recall, "mean_steps": lat / n}
