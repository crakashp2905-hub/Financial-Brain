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
    if task == "sentiment":
        return [{"state": r["headline"], "gold": r["gold"]} for r in
                json.loads((FIXTURES / "sentiment_labels.json").read_text("utf-8"))["rows"]]
    if task == "event_type":
        out = []
        for f in ("announcement_labels.json", "announcement_labels_holdout.json"):
            for r in json.loads((FIXTURES / f).read_text("utf-8"))["rows"]:
                out.append({"state": f"BSE subcategory: {r['subcategory']}\n"
                                     f"Subject: {r['subject']}\nHeadline: {r['headline']}",
                            "gold": r["gold_type"], "raw": r})
        return out
    raise ValueError(f"unknown task {task}")


def choices(task: str) -> list[str]:
    if task == "sentiment":
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
        if task != "sentiment":
            raise ValueError("finbert only does sentiment")
        from ..config import load
        fb = backends.FinBERT(load().data_root / "models" / "finbert")
        return lambda row: system1.finbert_decide(fb, row["state"])
    from ..llm import router
    if router.registry().get(model, {}).get("system_one", True) is False:
        raise ValueError(f"{model} is reasoning-only (system_one = false); it cannot give "
                         "a one-token typed decision")
    notes = SENTIMENT_NOTES if task == "sentiment" else None
    style = router.registry().get(model, {}).get("answer_style", "letters")
    return lambda row: system1.ollama_decide(model, INSTRUCTION[task], row["state"], ch,
                                             notes=notes, style=style)


def score(golds: list[str], decisions: list, target: float) -> dict:
    labels = sorted(set(golds) | {d.label for d in decisions})
    correct = [d.label == g for d, g in zip(decisions, golds)]
    f1s = []
    for lab in set(golds):
        tp = sum(1 for d, g in zip(decisions, golds) if d.label == lab == g)
        fp = sum(1 for d, g in zip(decisions, golds) if d.label == lab != g)
        fn = sum(1 for d, g in zip(decisions, golds) if g == lab != d.label)
        f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    # Per predicted label: the lowest confidence at which that label's confident
    # predictions were right >= target of the time (min MIN_SUPPORT of them). Overall
    # accuracy would let a model that confidently says "neutral" to everything qualify on
    # an imbalanced set while missing every adverse filing.
    thresholds = {}
    for lab in labels:
        preds = [(d.confidence, c) for d, c in zip(decisions, correct) if d.label == lab]
        thresholds[lab] = None
        for t in sorted({round(conf, 4) for conf, _ in preds}):
            kept = [c for conf, c in preds if conf >= t]
            if len(kept) >= MIN_SUPPORT and sum(kept) / len(kept) >= target:
                thresholds[lab] = t
                break
    accepted = [d for d in decisions if thresholds.get(d.label) is not None
                and d.confidence >= thresholds[d.label]]
    usable = [t for t in thresholds.values() if t is not None]
    confusion = Counter((g, d.label) for d, g in zip(decisions, golds) if d.label != g)
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
    out.update(task=task, model=model,
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


def simulate_route(con, task: str, steps: list[dict]) -> dict:
    """End-to-end result of a router cascade, replayed from stored benchmark items:
    accuracy, share answered by each step, share left uncertain, mean latency paid."""
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
    for i, g in enumerate(golds):
        label = None
        for s in steps:
            lab, conf, lat = items[s["model"]][i]
            paid += lat
            label = lab
            if accepts(s, lab, conf):
                answered_by[s["model"]] += 1
                break
        else:
            uncertain += 1
        right += label == g
    n = len(golds)
    return {"accuracy": right / n, "latency_ms": paid / n, "uncertain": uncertain / n,
            "answered_by": {m: c / n for m, c in answered_by.items()}}


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
        r.update(task=task, model=model, latency_ms=lat, items=items, rescored=True)
        con.execute("""INSERT INTO model_bench (run_at, task, model, n, accuracy, macro_f1,
                       latency_ms, target, threshold, coverage, detail)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    [datetime.now(timezone.utc), task, model, r["n"], r["accuracy"],
                     r["macro_f1"], lat, target, r["threshold"], r["coverage"],
                     json.dumps(r)])
        out.append(r)
    return out


def optimise_route(con, task: str, *, budget_ms: float = 4000, max_len: int = 3) -> dict:
    """Search chains (each ordered cheapest first) of up to ``max_len`` calibrated models
    and keep the one scoring best - accuracy minus the uncertain share, since an
    unresolved answer is no answer - within a mean-latency budget; ties go to speed."""
    from itertools import combinations

    from ..llm import router
    steps = router.plan(con, task, optimised=False)
    best = None
    for k in range(1, max_len + 1):
        for chain in combinations(steps, k):
            try:
                sim = simulate_route(con, task, list(chain))
            except ValueError:
                continue
            key = (sim["accuracy"] - sim["uncertain"], -sim["latency_ms"])
            if sim["latency_ms"] <= budget_ms and (best is None or key > best[0]):
                best = (key, list(chain), sim)
    if not best:
        return {"task": task, "chain": [], "note": f"nothing fits {budget_ms:.0f} ms"}
    return {"task": task, "chain": best[1], "simulated": best[2], "budget_ms": budget_ms}
